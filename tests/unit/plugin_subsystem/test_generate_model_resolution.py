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
"""

from __future__ import annotations

import json
from pathlib import Path

from daemon.plugin_subsystem.opendesign.generate import _build_openai_client

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


class TestDesignerReasoningModelPin:
    def test_designer_meta_llm_model_is_vision(self):
        """User directive: the designer agent's model is ``vision``.
        (Verified already satisfied in the repo — this pin keeps it.)"""
        meta = json.loads(
            (REPO_ROOT / "agents" / "designer" / "meta.json").read_text(encoding="utf-8")
        )
        assert meta["llm_model"] == "vision"
