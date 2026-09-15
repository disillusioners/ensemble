"""Config tests for the critical_notes section (Phase 1, D4).

Pins:

- Defaults when the yaml section is absent (always-on; no env vars).
- The shipped config.yaml block parses with the documented defaults.
- ``llm_select`` explicit resolver semantics (None/blank → False, bool
  passthrough, permissive vocabulary, invalid raises).
- N7 / D4 compliance: the reserved Phase-3 knob ``llm_select`` — like
  every knob in this section — has NO ``ENSEMBLE_*`` env var. The
  config class is a plain BaseModel (NOT BaseSettings), so no env
  binding exists by construction.
- Boot install: ``load_config`` propagates core_cap / reference_max /
  stale_days into the tool + render module caches and emits the boot
  INFO state line.
- Reserved Phase-2 fields ship with defaults only (no consumer machinery).
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from pydantic import BaseModel

from daemon.config import (
    CriticalNotesConfig,
    _resolve_critical_notes_llm_select,
    load_config,
)
from daemon.services.context_messages import (
    _resolve_critical_notes_reference_max,
)
from tests.helpers.critical_notes_fixtures import reset_module_state


@pytest.fixture
def config_file(tmp_path) -> Path:
    return tmp_path / "config.yaml"


def _write_minimal_config(tmp_path: Path, extra: str = "") -> Path:
    path = tmp_path / "config.yaml"
    # A non-empty yaml (a comment-only file parses to None and load_config
    # rejects it as empty by design).
    path.write_text(extra or "queue:\n  discard_on_startup: false\n")
    return path


class TestCriticalNotesConfigDefaults:
    def test_section_absent_yields_documented_defaults(self, tmp_path):
        config = load_config(_write_minimal_config(tmp_path))
        cn = config.critical_notes
        assert cn.core_cap == 8
        assert cn.tail_cap == 6
        assert cn.section_char_cap == 12000
        assert cn.fusion_bm25_weight == 0.4
        assert cn.fusion_vector_weight == 0.6
        assert cn.fusion_threshold == 0.30
        assert cn.floor_count == 2
        assert cn.reference_max == 500
        assert cn.query_max_chars == 2000
        assert cn.stale_days == 90
        assert cn.mint_cap_per_read == 10
        assert cn.llm_select is False  # reserved Phase 3

    def test_yaml_block_parses(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(
            "critical_notes:\n"
            "  core_cap: 5\n"
            "  reference_max: 250\n"
            "  stale_days: 30\n"
        )
        config = load_config(path)
        assert config.critical_notes.core_cap == 5
        assert config.critical_notes.reference_max == 250
        assert config.critical_notes.stale_days == 30

    def test_yaml_nulls_fall_back_to_defaults(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(
            "critical_notes:\n"
            "  core_cap: 6\n"
            "  reference_max:\n"
        )
        config = load_config(path)
        assert config.critical_notes.core_cap == 6
        assert config.critical_notes.reference_max == 500

    def test_non_dict_section_raises_loud_value_error(self, tmp_path):
        """A non-dict ``critical_notes:`` section (int / str / bool / list)
        used to silently fall through to defaults — masking operator typos.
        Now it must raise a named ``ValueError`` matching the crash-loud
        idiom of sibling config sections. Absent section still yields
        documented defaults (test_section_absent_yields_documented_defaults).
        """
        for bad in ("5", "true", "foo"):
            path = tmp_path / "config.yaml"
            path.write_text(f"critical_notes: {bad}\n")
            with pytest.raises(ValueError, match="must be a mapping"):
                load_config(path)


class TestNoEnvLayerD4:
    def test_config_class_binds_no_env_by_construction(self):
        # N7 / D4: plain BaseModel — no SettingsConfigDict, no env_prefix,
        # no env var of ANY spelling (ENSEMBLE_* or otherwise) can reach
        # these knobs.
        assert issubclass(CriticalNotesConfig, BaseModel)
        assert not hasattr(CriticalNotesConfig, "model_config") or not (
            CriticalNotesConfig.model_config or {}
        ).get("env_prefix")

    def test_env_vars_do_not_affect_knobs(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ENSEMBLE_CRITICAL_NOTES_TIERED", "0")  # v1 name — withdrawn
        monkeypatch.setenv("CRITICAL_NOTES_CORE_CAP", "99")
        monkeypatch.setenv("ENSEMBLE_CRITICAL_NOTES_CORE_CAP", "99")
        config = load_config(_write_minimal_config(tmp_path))
        assert config.critical_notes.core_cap == 8  # documented default, NOT 99


class TestLlmSelectResolver:
    def test_none_and_blank_default_false(self):
        assert _resolve_critical_notes_llm_select(None) is False
        assert _resolve_critical_notes_llm_select("") is False
        assert _resolve_critical_notes_llm_select("  ") is False

    def test_bool_passthrough(self):
        assert _resolve_critical_notes_llm_select(True) is True
        assert _resolve_critical_notes_llm_select(False) is False

    def test_permissive_vocabulary(self):
        assert _resolve_critical_notes_llm_select("true") is True
        assert _resolve_critical_notes_llm_select("ON") is True
        assert _resolve_critical_notes_llm_select("no") is False
        assert _resolve_critical_notes_llm_select("0") is False

    def test_invalid_raises_with_flag_name(self):
        with pytest.raises(ValueError, match="llm_select"):
            _resolve_critical_notes_llm_select("purple")

    def test_yaml_llm_select_true_flows_through(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text("critical_notes:\n  llm_select: true\n")
        assert load_config(path).critical_notes.llm_select is True


class TestBootInstallAndInfoLine:
    def test_load_config_installs_tool_and_render_knobs(self, tmp_path, caplog):
        path = tmp_path / "config.yaml"
        path.write_text(
            "critical_notes:\n"
            "  core_cap: 4\n"
            "  reference_max: 111\n"
            "  stale_days: 45\n"
        )
        with caplog.at_level(logging.INFO):
            config = load_config(path)
        # Render module cache sees the installed bound.
        assert _resolve_critical_notes_reference_max() == 111
        assert config.critical_notes.core_cap == 4
        assert "[CriticalNotes]" in caplog.text
        assert "core_cap=4" in caplog.text
        assert "reference_max=111" in caplog.text
        # The boot line is state VISIBILITY, not a switch (D4).
        assert "always-on per D4" in caplog.text
