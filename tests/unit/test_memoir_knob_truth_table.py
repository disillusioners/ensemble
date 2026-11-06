"""The memoir knob truth table — the six rows, exactly (phase 0).

Plan: ``.agents/shared/planning/ari-memoir-store/phase0-plan.md`` AC3/AC5/AC6
(pack: ``test/packs/memoir_knob_unit_test.sh``).

Every row of the canonical truth table gets its own assertion, including
the log LEVEL and the log CONTENT (the truth table says the WARNING names
the var, the rejected value and the canonical spelling — the content is
part of the contract, not a nicety).

The MUTATION-sensitive assertion is
:func:`test_indeterminate_value_fails_closed`: the pack plants
"garbage is repaired to the default" into
``daemon/config.py::classify_memoir_enabled`` and this test MUST go RED.
"""

from __future__ import annotations

import logging
import os
import textwrap

import pytest

from daemon.config import (
    MEMOIR_ENABLED_ENV_VAR,
    MEMOIR_RESOLUTION_CONFIG,
    MEMOIR_RESOLUTION_DEFAULT,
    MEMOIR_RESOLUTION_ERROR,
    MEMOIR_RESOLUTION_EXPLICIT_OFF,
    MEMOIR_RESOLUTION_EXPLICIT_ON,
    MEMOIR_RESOLUTION_INDETERMINATE,
    Config,
    load_config,
)
from daemon.tools import memoir_knob
from daemon.tools.memoir_knob import (
    memoir_enabled,
    memoir_tool_help,
    reset_boot_log_state,
    resolve_memoir_knob,
)

KNOB_LOGGER = "daemon.tools.memoir_knob"


@pytest.fixture(autouse=True)
def _clean_memoir_env(monkeypatch):
    """No ambient ``ENSEMBLE_MEMOIR_*`` leaks into an assertion."""
    for key in list(os.environ):
        if key.startswith("ENSEMBLE_MEMOIR_"):
            monkeypatch.delenv(key, raising=False)
    reset_boot_log_state()
    yield
    reset_boot_log_state()


def _records(caplog, level: int) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == level]


# ── Row 1: absent ─────────────────────────────────────────────────────────


def test_absent_env_enables_with_one_warning_naming_the_var(caplog):
    """Absent ⇒ ENABLED + exactly one WARNING naming the variable."""
    with caplog.at_level(logging.DEBUG, logger=KNOB_LOGGER):
        state = resolve_memoir_knob()

    assert state.enabled is True
    assert state.resolution == MEMOIR_RESOLUTION_DEFAULT
    assert state.rejected_value is None

    warnings = _records(caplog, logging.WARNING)
    assert len(warnings) == 1, warnings
    message = warnings[0]
    assert MEMOIR_ENABLED_ENV_VAR in message
    assert "ENABLED" in message
    assert "unset" in message
    # No ERROR and no INFO for the unset case.
    assert _records(caplog, logging.ERROR) == []
    assert _records(caplog, logging.INFO) == []


# ── Row 2: empty string == absent ─────────────────────────────────────────


def test_empty_string_is_absent(caplog):
    """An empty value is "= absent": ENABLED, same resolution, WARNING."""
    os.environ[MEMOIR_ENABLED_ENV_VAR] = ""
    with caplog.at_level(logging.DEBUG, logger=KNOB_LOGGER):
        state = resolve_memoir_knob()

    assert state.enabled is True
    assert state.resolution == MEMOIR_RESOLUTION_DEFAULT
    warnings = _records(caplog, logging.WARNING)
    assert len(warnings) == 1
    assert MEMOIR_ENABLED_ENV_VAR in warnings[0]


# ── Row 3: explicit truthy ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw",
    ["true", "TRUE", "True", "1", "yes", "YES", "on", "ON", "t", "T", "y", "Y"],
)
def test_explicit_truthy_enables_with_info(raw, caplog):
    """Every canonical truthy spelling ⇒ ENABLED + INFO (no WARNING)."""
    os.environ[MEMOIR_ENABLED_ENV_VAR] = raw
    with caplog.at_level(logging.DEBUG, logger=KNOB_LOGGER):
        state = resolve_memoir_knob()

    assert state.enabled is True
    assert state.resolution == MEMOIR_RESOLUTION_EXPLICIT_ON
    assert state.source == "env"
    assert _records(caplog, logging.WARNING) == []
    assert _records(caplog, logging.ERROR) == []
    infos = _records(caplog, logging.INFO)
    assert len(infos) == 1
    assert MEMOIR_ENABLED_ENV_VAR in infos[0]


# ── Row 4: explicit falsy ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw",
    ["false", "FALSE", "False", "0", "no", "NO", "off", "OFF", "f", "F", "n", "N"],
)
def test_explicit_falsy_disables_with_info_naming_the_var(raw, caplog):
    """Every canonical falsy spelling ⇒ DISABLED + INFO naming the var."""
    os.environ[MEMOIR_ENABLED_ENV_VAR] = raw
    with caplog.at_level(logging.DEBUG, logger=KNOB_LOGGER):
        state = resolve_memoir_knob()

    assert state.enabled is False
    assert state.resolution == MEMOIR_RESOLUTION_EXPLICIT_OFF
    assert state.source == "env"
    assert _records(caplog, logging.WARNING) == []
    assert _records(caplog, logging.ERROR) == []
    infos = _records(caplog, logging.INFO)
    assert len(infos) == 1
    assert MEMOIR_ENABLED_ENV_VAR in infos[0]
    assert "memoir_disabled" in infos[0]


# ── Row 5: indeterminate ⇒ fail CLOSED ────────────────────────────────────


def test_indeterminate_value_fails_closed(caplog):
    """MUTATION-SENSITIVE: a garbage value resolves to DISABLED.

    The planted defect (garbage repaired to the default) flips this
    assertion RED: the resolved state would be True instead of False.
    """
    os.environ[MEMOIR_ENABLED_ENV_VAR] = "maybe"
    with caplog.at_level(logging.DEBUG, logger=KNOB_LOGGER):
        state = resolve_memoir_knob()

    assert state.enabled is False, "an indeterminate value must fail CLOSED"
    assert state.resolution == MEMOIR_RESOLUTION_INDETERMINATE
    assert state.rejected_value == "maybe"

    warnings = _records(caplog, logging.WARNING)
    assert len(warnings) == 1
    message = warnings[0]
    # The WARNING names the var, the rejected value AND the canonical
    # spelling — all three are part of the contract.
    assert MEMOIR_ENABLED_ENV_VAR in message
    assert "maybe" in message
    assert "true/false" in message
    assert _records(caplog, logging.ERROR) == []


def test_indeterminate_value_never_raises():
    """A garbage value never raises — a raise would abort the boot."""
    os.environ[MEMOIR_ENABLED_ENV_VAR] = "perhaps"
    state = resolve_memoir_knob()  # must not raise
    assert state.enabled is False


# ── Row 6: unparseable AND the fallback also fails ⇒ ERROR ────────────────


def test_unparseable_without_config_fallback_is_error(caplog):
    """The env tier is unparseable AND no config tier ⇒ DISABLED + ERROR."""
    os.environ["ENSEMBLE_MEMOIR_EMBEDDING_TICK_S"] = "not-an-int"
    with caplog.at_level(logging.DEBUG, logger=KNOB_LOGGER):
        state = resolve_memoir_knob(config=None)

    assert state.enabled is False
    assert state.resolution == MEMOIR_RESOLUTION_ERROR
    errors = _records(caplog, logging.ERROR)
    assert len(errors) == 1
    assert MEMOIR_ENABLED_ENV_VAR in errors[0]
    assert "DISABLED" in errors[0]


def test_unparseable_with_config_fallback_uses_the_config_tier(caplog):
    """Unparseable env tier + an available config tier ⇒ the config tier.

    The config object is built BEFORE the env is poisoned: the config
    tier is itself a ``BaseSettings``, so a garbage env value would
    poison it too. The fallback is therefore only usable when the
    config object predates the corruption (which is the realistic
    shape — the daemon's config is resolved at boot).
    """
    config = Config(memoir={"enabled": False})
    os.environ["ENSEMBLE_MEMOIR_EMBEDDING_TICK_S"] = "not-an-int"
    with caplog.at_level(logging.DEBUG, logger=KNOB_LOGGER):
        state = resolve_memoir_knob(config=config)

    assert state.enabled is False
    assert state.resolution != MEMOIR_RESOLUTION_ERROR
    assert _records(caplog, logging.ERROR) == []
    assert len(_records(caplog, logging.WARNING)) == 1


# ── AC3: the config-file tier ─────────────────────────────────────────────


def test_config_yaml_disables_the_surface_when_the_env_is_absent(caplog, tmp_path):
    """``config.yaml memoir.enabled: false`` is honoured when the env is absent."""
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(
        textwrap.dedent(
            """
            memoir:
              enabled: false
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    config = load_config(str(yaml_path))
    assert config.memoir.enabled is False

    with caplog.at_level(logging.DEBUG, logger=KNOB_LOGGER):
        state = resolve_memoir_knob(config=config)

    assert state.enabled is False
    assert state.resolution == MEMOIR_RESOLUTION_CONFIG
    assert _records(caplog, logging.WARNING) == []
    assert _records(caplog, logging.ERROR) == []


def test_env_tier_beats_the_config_file_tier(tmp_path):
    """The kill-switch env var overrides ``config.yaml``."""
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text("memoir:\n  enabled: false\n", encoding="utf-8")
    config = load_config(str(yaml_path))
    assert config.memoir.enabled is False

    os.environ[MEMOIR_ENABLED_ENV_VAR] = "true"
    state = resolve_memoir_knob(config=config)
    assert state.enabled is True
    assert state.resolution == MEMOIR_RESOLUTION_EXPLICIT_ON


# ── AC5: discoverability without reading logs ─────────────────────────────


def test_resolved_state_is_discoverable_without_reading_logs():
    """The resolved state is retrievable from the tool_help text."""
    os.environ[MEMOIR_ENABLED_ENV_VAR] = "false"
    text = memoir_tool_help()
    assert "DISABLED" in text
    assert MEMOIR_ENABLED_ENV_VAR in text
    assert "resolution=explicit_off" in text

    os.environ[MEMOIR_ENABLED_ENV_VAR] = "true"
    text = memoir_tool_help()
    assert "ENABLED" in text
    assert "resolution=explicit_on" in text


def test_tool_help_text_is_owned_by_the_knob_module():
    """The discoverability wording has exactly one home (the knob module)."""
    assert "KNOB TRUTH TABLE" in (memoir_knob.__doc__ or "")
    assert callable(memoir_knob.memoir_tool_help)


# ── AC6: the call-time read ───────────────────────────────────────────────


def test_knob_is_read_at_call_time_within_one_process():
    """Flipping the env between two calls changes the answer — no restart."""
    os.environ[MEMOIR_ENABLED_ENV_VAR] = "true"
    assert memoir_enabled() is True

    os.environ[MEMOIR_ENABLED_ENV_VAR] = "false"
    assert memoir_enabled() is False

    del os.environ[MEMOIR_ENABLED_ENV_VAR]
    assert memoir_enabled() is True


def test_knob_value_is_never_cached_at_import_time():
    """The module holds no cached resolved VALUE."""
    source = (memoir_knob.__file__ or "")
    with open(source, "r", encoding="utf-8") as handle:
        text = handle.read()
    # The only module-level cache is the LOG dedupe set, which is
    # cleared by reset_boot_log_state and never holds the value.
    assert "_ENABLED_CACHE" not in text
    assert "_EMITTED: set[str]" in text


# ── the log guard (once per boot) ─────────────────────────────────────────


def test_the_unset_warning_is_emitted_once_per_boot(caplog):
    """Repeated resolutions of the same state do not re-warn."""
    with caplog.at_level(logging.DEBUG, logger=KNOB_LOGGER):
        resolve_memoir_knob()
        resolve_memoir_knob()
        resolve_memoir_knob()
    assert len(_records(caplog, logging.WARNING)) == 1


def test_a_changed_knob_value_re_warns(caplog):
    """A changed value produces a different message ⇒ it re-warns."""
    with caplog.at_level(logging.DEBUG, logger=KNOB_LOGGER):
        resolve_memoir_knob()
        os.environ[MEMOIR_ENABLED_ENV_VAR] = "maybe"
        resolve_memoir_knob()
    assert len(_records(caplog, logging.WARNING)) == 2


# ── Tidier item 14 (B3): non-string types FAIL CLOSED, never raise ────────


def test_non_string_garbage_types_fail_closed_instead_of_raising():
    """A list/dict/float ``enabled`` is garbage ⇒ DISABLED, not a crash.

    The pre-fix tail ``return value`` handed these straight to Pydantic,
    which raised ``ValidationError`` — a config-file list would abort
    the BOOT instead of failing the surface closed (DR-3). Pinned so the
    pass-through cannot return.
    """
    from daemon.config import MemoirConfig

    for garbage in ([1, 2], {"on": True}, 3.5):
        config = MemoirConfig(enabled=garbage)
        assert config.enabled is False, f"{garbage!r} must fail CLOSED"


def test_yaml_integer_scalars_keep_their_meaning():
    """``enabled: 0`` / ``enabled: 1`` are conventional YAML booleans.

    They keep their meaning (0=off, 1=on); any OTHER integer is out of
    domain and fails closed. This is the boundary the fix deliberately
    preserves so an upgrade cannot silently disable a working surface.
    """
    from daemon.config import MemoirConfig

    assert MemoirConfig(enabled=1).enabled is True
    assert MemoirConfig(enabled=0).enabled is False
    for out_of_domain in (-1, 2, 7):
        assert MemoirConfig(enabled=out_of_domain).enabled is False
