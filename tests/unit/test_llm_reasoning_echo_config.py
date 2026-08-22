"""Unit tests for LLMConfig.reasoning_echo_disabled_models parsing.

The ``OPENAI_REASONING_ECHO_DISABLED_MODELS`` env var (or YAML
``llm.reasoning_echo_disabled_models``) controls which model name patterns
are EXCLUDED from ``reasoning_content`` echo in multi-turn requests. All
other models echo. Operators can supply:

  - A JSON list: ``OPENAI_REASONING_ECHO_DISABLED_MODELS='["gpt-4o", "glm"]'``
  - A comma-separated string: ``OPENAI_REASONING_ECHO_DISABLED_MODELS="gpt-4o,glm"``
  - A Python list via YAML config

Default: ``[]`` (all models echo).
"""

import pytest
from pydantic import ValidationError

from daemon.config import LLMConfig


class TestReasoningEchoDisabledModelsDefault:
    """The default must be [] when no env / config is provided."""

    def test_default_is_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Clear any OPENAI_REASONING_ECHO_DISABLED_MODELS that might leak from the host env
        monkeypatch.delenv("OPENAI_REASONING_ECHO_DISABLED_MODELS", raising=False)
        cfg = LLMConfig(_env_file=None)
        assert cfg.reasoning_echo_disabled_models == []


class TestReasoningEchoDisabledModelsFromList:
    """Python lists (from YAML) are passed through unchanged."""

    def test_list_passthrough(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_REASONING_ECHO_DISABLED_MODELS", raising=False)
        cfg = LLMConfig(reasoning_echo_disabled_models=["gpt-4o", "glm", "claude"], _env_file=None)
        assert cfg.reasoning_echo_disabled_models == ["gpt-4o", "glm", "claude"]


class TestReasoningEchoDisabledModelsFromCommaString:
    """Comma-separated env values are split on commas and stripped."""

    def test_two_patterns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_REASONING_ECHO_DISABLED_MODELS", "gpt-4o,glm")
        cfg = LLMConfig(_env_file=None)
        assert cfg.reasoning_echo_disabled_models == ["gpt-4o", "glm"]

    def test_single_pattern(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_REASONING_ECHO_DISABLED_MODELS", "gpt-4o")
        cfg = LLMConfig(_env_file=None)
        assert cfg.reasoning_echo_disabled_models == ["gpt-4o"]

    def test_whitespace_stripped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_REASONING_ECHO_DISABLED_MODELS", " gpt-4o , glm ")
        cfg = LLMConfig(_env_file=None)
        assert cfg.reasoning_echo_disabled_models == ["gpt-4o", "glm"]

    def test_empty_string_yields_empty_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_REASONING_ECHO_DISABLED_MODELS", "")
        cfg = LLMConfig(_env_file=None)
        assert cfg.reasoning_echo_disabled_models == []


class TestReasoningEchoDisabledModelsFromJsonString:
    """JSON-formatted env values are passed through to pydantic's list parser."""

    def test_json_array(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_REASONING_ECHO_DISABLED_MODELS", '["gpt-4o", "glm"]')
        cfg = LLMConfig(_env_file=None)
        assert cfg.reasoning_echo_disabled_models == ["gpt-4o", "glm"]


class TestReasoningEchoDisabledModelsInvalid:
    """Non-string, non-list values are rejected by pydantic."""

    def test_int_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_REASONING_ECHO_DISABLED_MODELS", raising=False)
        with pytest.raises(ValidationError):
            LLMConfig(reasoning_echo_disabled_models=42, _env_file=None)  # type: ignore[arg-type]
