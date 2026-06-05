"""Unit tests for LLMConfig.reasoning_echo_models parsing.

The ``OPENAI_REASONING_ECHO_MODELS`` env var (or YAML ``llm.reasoning_echo_models``)
controls which model name patterns get their ``reasoning_content`` echoed back
in multi-turn requests. Operators can supply:

  - A JSON list: ``OPENAI_REASONING_ECHO_MODELS='["deepseek", "glm"]'``
  - A comma-separated string: ``OPENAI_REASONING_ECHO_MODELS="deepseek,glm"``
  - A Python list via YAML config

Default: ``["deepseek"]`` (DeepSeek's thinking-mode API requires echo for
tool-calling turns).
"""

import pytest
from pydantic import ValidationError

from daemon.config import LLMConfig


class TestReasoningEchoModelsDefault:
    """The default must be ['deepseek'] when no env / config is provided."""

    def test_default_is_deepseek(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Clear any OPENAI_REASONING_ECHO_MODELS that might leak from the host env
        monkeypatch.delenv("OPENAI_REASONING_ECHO_MODELS", raising=False)
        cfg = LLMConfig(_env_file=None)
        assert cfg.reasoning_echo_models == ["deepseek"]


class TestReasoningEchoModelsFromList:
    """Python lists (from YAML) are passed through unchanged."""

    def test_list_passthrough(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_REASONING_ECHO_MODELS", raising=False)
        cfg = LLMConfig(reasoning_echo_models=["deepseek", "glm", "zai"], _env_file=None)
        assert cfg.reasoning_echo_models == ["deepseek", "glm", "zai"]


class TestReasoningEchoModelsFromCommaString:
    """Comma-separated env values are split on commas and stripped."""

    def test_two_patterns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_REASONING_ECHO_MODELS", "deepseek,glm")
        cfg = LLMConfig(_env_file=None)
        assert cfg.reasoning_echo_models == ["deepseek", "glm"]

    def test_single_pattern(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_REASONING_ECHO_MODELS", "deepseek")
        cfg = LLMConfig(_env_file=None)
        assert cfg.reasoning_echo_models == ["deepseek"]

    def test_whitespace_stripped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_REASONING_ECHO_MODELS", " deepseek , glm ")
        cfg = LLMConfig(_env_file=None)
        assert cfg.reasoning_echo_models == ["deepseek", "glm"]

    def test_empty_string_yields_empty_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_REASONING_ECHO_MODELS", "")
        cfg = LLMConfig(_env_file=None)
        assert cfg.reasoning_echo_models == []


class TestReasoningEchoModelsFromJsonString:
    """JSON-formatted env values are passed through to pydantic's list parser."""

    def test_json_array(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_REASONING_ECHO_MODELS", '["deepseek", "glm"]')
        cfg = LLMConfig(_env_file=None)
        assert cfg.reasoning_echo_models == ["deepseek", "glm"]


class TestReasoningEchoModelsInvalid:
    """Non-string, non-list values are rejected by pydantic."""

    def test_int_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_REASONING_ECHO_MODELS", raising=False)
        with pytest.raises(ValidationError):
            LLMConfig(reasoning_echo_models=42, _env_file=None)  # type: ignore[arg-type]
