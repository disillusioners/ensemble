"""Pinned tests: the designer GENERATION model resolves to ``vision``.

Incident 2026-10-07 (designer-model-vision-fix). The live v0.18.0
designer debut (settings-page-redesign, plugin lane) generated with
the daemon's default-pool CHAT model ``agentic`` instead of the
design-generation model ``vision``:

  daemon/tools/instance.py:build_plugin_tools() call site passes no
  ``env`` -> plugin_tool_factory forwards ``env=None`` ->
  OdGenerate.execute(env=None) -> _build_openai_client reads
  ``os.environ`` -> the old resolution read ``OPENAI_MODEL`` (the
  daemon's chat model env var, = ``agentic`` in the live .env), so
  the ``default "vision"`` fallback never fired and the "upstream
  timeout" attribution in OD-LANE-FAILURE.md was wrong-model, not
  upstream capacity.

The MCP-era lane resolved the model from the OD daemon's BYOK config
(``BYOK_MODEL=vision``); after slice-⑦ retired the MCP seam, the
native lane resolves the model itself. The fix pins the resolution to
the PURPOSE-BOUND design knob ``OPENAI_MODEL_VISION`` (per the
config.yaml model-scope convention: purpose-bound models never ride
the main OPENAI_MODEL), defaulting to the literal ``vision``.

Offline-first: no network; ``openai.OpenAI`` construction is offline.

Stage 1 (od-generate-agent-lane): the same openai-client-construction
path is preserved (Stage 1 reuses it as a v1 seam; the v2 prod path
routes through :func:`_invoke_chat_via_facade` via the new
``_LLM_INVOKER`` test seam). The model-resolution regressions below
remain green either way: ``_resolve_llm_config`` reads OPENAI_MODEL_VISION
first, ``OPENAI_MODEL`` is NEVER consulted.
"""

from __future__ import annotations

import json
from pathlib import Path

from daemon.plugin_subsystem.opendesign.generate import (
    _PROXY_IDENTITY_HEADERS,
    _build_openai_client,
    _resolve_llm_config,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

_BASE = {
    "OPENAI_BASE_URL": "http://fake.test/v1",
    "OPENAI_API_KEY": "fake-key",
}


class TestGenerationModelResolution:
    def test_live_leak_shape_resolves_vision_not_chat_model(self):
        """THE regression pin: the exact live .env shape that leaked.

        Daemon .env carries OPENAI_MODEL=agentic (chat default pool)
        AND OPENAI_MODEL_VISION=vision. The old code returned
        ``agentic`` here; the fix must return ``vision``.
        """
        env = dict(_BASE, OPENAI_MODEL="agentic", OPENAI_MODEL_VISION="vision")
        _client, model = _build_openai_client(env)
        assert model == "vision"
        assert model != "agentic"

    def test_generation_never_inherits_chat_model_when_knob_absent(self):
        """OPENAI_MODEL (the chat model) must NEVER be read, even when
        OPENAI_MODEL_VISION is unset — the fallback is the literal
        design-generation model, not the default-pool chat model."""
        env = dict(_BASE, OPENAI_MODEL="agentic")
        _client, model = _build_openai_client(env)
        assert model == "vision"

    def test_generation_model_env_knob_honored(self):
        """The purpose-bound knob is the single resolution input."""
        env = dict(_BASE, OPENAI_MODEL_VISION="vision")
        _client, model = _build_openai_client(env)
        assert model == "vision"


class TestRawLLMConfigPassThrough:
    """Stage 1 (Slice 1 — Plan §5.1, prescription 2): ``_resolve_llm_config``
    passes the RAW config dict the facade expects, including the
    ``base_url_backup`` slot. The graph path's ``clean_llm_config``
    strips ``base_url_backup`` and would silently kill failover
    (graph.py:3885-3889); the raw-SDK facade reads the BACKUP directly
    from the dict it receives, so we pass it through untransformed.
    """

    def test_base_url_backup_passthrough_when_configured(self):
        """When OPENAI_BASE_URL_BACKUP is set, the raw llm_config dict
        carries it intact — the facade's _RawFailoverShim activates."""
        env = dict(
            _BASE,
            OPENAI_BASE_URL_BACKUP="http://backup.test/v1",
        )
        cfg = _resolve_llm_config(env)
        assert cfg["base_url_backup"] == "http://backup.test/v1"
        assert cfg["base_url"] == "http://fake.test/v1"
        assert cfg["api_key"] == "fake-key"
        assert cfg["model"] == "vision"

    def test_base_url_backup_is_none_when_unset(self):
        """Decision D3 open: OPENAI_BASE_URL_BACKUP is unset on this
        deployment → failover is INERT. The dict carries ``None`` so
        the facade builds with ``failover_controller=None`` and every
        retry is bounded-retry-on-primary only."""
        env = dict(_BASE)
        cfg = _resolve_llm_config(env)
        assert cfg["base_url_backup"] is None

    def test_byok_not_configured_when_base_url_missing(self):
        """Missing OPENAI_BASE_URL or OPENAI_API_KEY surfaces as the
        byok_not_configured envelope — same shape as pre-v2."""
        import pytest
        with pytest.raises(RuntimeError, match="byok_not_configured"):
            _resolve_llm_config({"OPENAI_API_KEY": "x"})  # no base_url


class TestProxyIdentityHeadersConstant:
    """Stage 1 (prescription 4): proxy identity headers carry the
    same stamp the agent-chat hot path rides (snapshot_embedding_service
    + 6 inline sites). Verify the constant holds the exact header
    names/values the proxy expects."""

    def test_x_proxy_app_header_name_and_value(self):
        assert "x-proxy-app" in _PROXY_IDENTITY_HEADERS
        assert _PROXY_IDENTITY_HEADERS["x-proxy-app"] == "ensemble"

    def test_x_proxy_interleaved_thinking_header_name_and_value(self):
        assert "x-proxy-interleaved-thinking" in _PROXY_IDENTITY_HEADERS
        assert _PROXY_IDENTITY_HEADERS["x-proxy-interleaved-thinking"] == "True"


class TestDesignerReasoningModelPin:
    def test_designer_meta_llm_model_is_vision(self):
        """User directive: the designer agent's model is ``vision``.
        (Verified already satisfied in the repo — this pin keeps it.)"""
        meta = json.loads(
            (REPO_ROOT / "agents" / "designer" / "meta.json").read_text(encoding="utf-8")
        )
        assert meta["llm_model"] == "vision"
