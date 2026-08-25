"""Tests for clean_llm_config helper in daemon/graph.py.

The helper strips non-kwarg keys (currently only ``model_vision``) from
``llm_config`` dicts before they are splatted into ``ThinkingChatOpenAI(**cfg)``.
``model_vision`` is configuration metadata used for vision routing decisions,
not a valid LangChain/ChatOpenAI parameter, and must be removed to avoid
``Completions.create() got an unexpected keyword argument 'model_vision'``
errors and noisy ``UserWarning``s about it being transferred to ``model_kwargs``.
"""


class TestCleanLlmConfig:
    """Tests for clean_llm_config()."""

    def test_clean_llm_config_strips_model_vision(self):
        """clean_llm_config should strip model_vision but preserve all other keys."""
        from daemon.graph import clean_llm_config

        original = {
            "model": "gpt-4o",
            "base_url": "http://localhost:8080",
            "api_key": "sk-test",
            "temperature": 0.7,
            "model_vision": "gpt-4o-mini",
        }
        cleaned = clean_llm_config(original)

        # model_vision removed
        assert "model_vision" not in cleaned

        # All other keys preserved
        assert cleaned["model"] == "gpt-4o"
        assert cleaned["base_url"] == "http://localhost:8080"
        assert cleaned["api_key"] == "sk-test"
        assert cleaned["temperature"] == 0.7

        # Input not mutated
        assert "model_vision" in original
        assert original["model_vision"] == "gpt-4o-mini"

    def test_clean_llm_config_without_model_vision(self):
        """clean_llm_config should work fine if model_vision is already absent.

        Note: ``clean_llm_config`` also injects ``streaming`` (the class-
        level ``ThinkingChatOpenAI.default_streaming``) when the key is
        absent — see the CF-125s 524 fix. Asserting exact dict equality
        would break that injection; assert original-key survival instead,
        plus the streaming default.
        """
        from daemon.graph import ThinkingChatOpenAI, clean_llm_config

        original = {"model": "gpt-4o", "api_key": "sk-test"}
        cleaned = clean_llm_config(original)
        # All original keys survive (model_vision absent — no strip to verify)
        assert cleaned["model"] == "gpt-4o"
        assert cleaned["api_key"] == "sk-test"
        # Streaming default injected from class var (operators flip via
        # OPENAI_STREAMING=false, propagated to the class var at startup).
        assert cleaned["streaming"] is ThinkingChatOpenAI.default_streaming
        assert cleaned is not original  # Should be a new dict
