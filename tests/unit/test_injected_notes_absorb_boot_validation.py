"""Boot-time validation for ``ENSEMBLE_INJECTED_NOTES_ABSORB``.

Companion to ``tests/unit/services/test_injected_notes_hoisting.py`` (resolver-level
pins). Pins the contract that ``daemon.config.load_config`` validates the
``ENSEMBLE_INJECTED_NOTES_ABSORB`` env var at startup so a garbage value
(e.g. ``purple``) raises ``ValueError`` naming the flag during daemon boot,
not mid-flight on the CLE recovery turn. Mirrors the
``tests/unit/test_compaction_model_config.py`` / ``test_llm_allowed_models_precedence.py``
precedent — env-driven resolvers invoked explicitly in ``load_config`` to
outflank the pydantic init-kwarg-beats-env inversion.

Contract:

  * unset / empty string / valid value (0/1/true/false/yes/no/on/off, any
    case) → ``load_config`` succeeds (the documented default applies).
  * garbage value (e.g. ``purple``, ``maybe``) → ``load_config`` raises
    ``ValueError`` whose message contains ``ENSEMBLE_INJECTED_NOTES_ABSORB``.

The resolver itself (``resolve_injected_notes_absorb``) is exercised in
``test_injected_notes_hoisting.py::TestAbsorbKillSwitchResolver``. This file
focuses on the ``load_config`` end-to-end seam so the boot-time validation
contract is pinned independent of the resolver's own tests.
"""

from __future__ import annotations

import pytest

from daemon.config import load_config


_FLAG = "ENSEMBLE_INJECTED_NOTES_ABSORB"


def _write_yaml(tmp_path) -> str:
    """Minimal loadable config.yaml (no compaction key needed — the kill-switch
    is env-only by design; see ``resolve_injected_notes_absorb`` docstring)."""
    text = """
llm:
  base_url: "https://api.openai.com/v1"
  api_key: "test-key"
  model: "gpt-4"

persistence:
  db_path: "./data/instances.db"
"""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(text)
    return str(config_file)


class TestLoadConfigInjectedNotesAbsorbBootValidation:
    """``load_config`` must validate ``ENSEMBLE_INJECTED_NOTES_ABSORB`` at
    startup so a garbage env value never reaches the mid-flight CLE
    recovery turn. Pinned via ``monkeypatch`` (per-test env isolation) +
    a tmp yaml (the resolver reads only env; the yaml shape stays
    minimal so the test isn't accidentally shadowed by a yaml key)."""

    def test_valid_value_loads(self, tmp_path, monkeypatch) -> None:
        """A canonical truthy value resolves cleanly and ``load_config``
        returns a ``Config`` without raising."""
        monkeypatch.setenv(_FLAG, "1")
        config = load_config(config_path=_write_yaml(tmp_path))
        assert config is not None

    def test_flag_unset_loads(self, tmp_path, monkeypatch) -> None:
        """Unset → documented ON default (resolver treats unset as True);
        ``load_config`` must not raise."""
        monkeypatch.delenv(_FLAG, raising=False)
        config = load_config(config_path=_write_yaml(tmp_path))
        assert config is not None

    def test_flag_empty_string_loads(self, tmp_path, monkeypatch) -> None:
        """Bare ``KEY=`` in ``.env`` reaches ``os.environ`` as ``""`` —
        ``_clean_env_value`` normalizes to UNSET → documented ON default.
        ``load_config`` must not raise."""
        monkeypatch.setenv(_FLAG, "")
        config = load_config(config_path=_write_yaml(tmp_path))
        assert config is not None

    def test_garbage_value_raises_naming_flag(
        self, tmp_path, monkeypatch
    ) -> None:
        """``purple`` is neither falsy (``0``/``false``/``no``/``off``) nor
        truthy (``1``/``true``/``yes``/``on``); the resolver raises
        ``ValueError`` and the message MUST contain the flag name so an
        operator can find it in the boot log. ``load_config`` propagates
        this verbatim — no swallowing, no silent default."""
        monkeypatch.setenv(_FLAG, "purple")
        with pytest.raises(ValueError, match=_FLAG):
            load_config(config_path=_write_yaml(tmp_path))