"""Phase-2 chain-invariant + defaults tests (plan od-generate-async-poll §6.2, §7.6).

The ATOMIC budget-chain reconciliation for the 200k target: any link
left behind silently becomes the new min() killer (the Oct-9 failure
family). Asserted constants are split by WHERE they can be asserted
from (plan v1.2 #6):

- CODE-ASSERTABLE (this file): ``wall_clock_cap_s == 600``;
  the max_tokens default resolves to DEFAULT_MAX_TOKENS (200000) at
  every live injection site — (i) the StructuredTool production entry
  with ``max_tokens`` omitted, (ii) ``execute_dict({})`` raw-dict
  fallback, (iii) the dataclass default, (iv) the invalid-value clamp
  fallback; the no-residual-literal pin (no ``64000`` literal in
  generate.py / ports.py); the pipeline dispatch-wait constant ≥ 660 s;
  the invariant shape ``wall < wait`` with ``wait ≥ wall + 60 s``.
- DOC-PINNED / DEPLOY-GATED (deliberately NOT asserted here): the
  proxy-side constants — ``MAX_GENERATION_TIME ≥ 900 s``, the
  WriteTimeout derivation (``max(5min, 3×MGT)`` clamped at 30 min),
  ``StreamDeadline`` (110 s TTFB bound), ``IdleTerminationTimeout``
  (120 s inter-chunk bound). None of these are readable from ensemble
  tests; they are asserted at deploy time via helm-values review +
  the proxy repo's own config tests, and behaviorally exercised by the
  §7.1/§7.8 tests. Asserting anything fake about them here would be a
  lie, not a pin.

Wiring note (plan-vs-code drift, verified empirically): langchain_core
keeps the Port's dict ``inputs_schema`` verbatim on the StructuredTool
— NO schema-default injection happens at the tool layer, so when the
agent omits ``max_tokens`` the OPERATIVE default is generate.py's
``execute_dict`` raw-dict fallback (site ii). The observable is
identical (omitted → DEFAULT_MAX_TOKENS) and both sites pin to the
same constant, so the chain holds either way; the ports.py schema
default is asserted at the data level below.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from daemon.plugin_subsystem.opendesign.generate import GenerateInput
from daemon.plugin_subsystem.plugin_tool_factory import build_tools_for_port
from daemon.plugin_subsystem.port_registry import validate_ports
from daemon.plugin_subsystem.opendesign.ports import (
    DEFAULT_MAX_TOKENS,
    declared_opendesign_ports,
)
from daemon.tools.compare_tools import _COMPARATOR_DISPATCH_WAIT_S

_GENERATE_MOD = "daemon/plugin_subsystem/opendesign/generate.py"
_PORTS_MOD = "daemon/plugin_subsystem/opendesign/ports.py"

# The §6.2 chain, code-assertable side (decision (a)):
_WALL_EXPECTED = 600.0
_WAIT_MINIMUM = 660.0  # wall + 60s strict margin


@pytest.fixture
def env():
    """Offline OPENAI_* env (mirrors test_opendesign_b_element.env)."""
    return {
        "OPENAI_BASE_URL": "http://fake.test/v1",
        "OPENAI_API_KEY": "fake-key",
        "OPENAI_MODEL_VISION": "vision",
    }


def _generate_tool():
    ports, _ = validate_ports(declared_opendesign_ports())
    gen_port = next(p for p in ports if p.port_id == "od.generate")
    return build_tools_for_port(gen_port)[0]


class TestChainInvariant:
    """The invariant shape: ``wall < wait`` with ``wait ≥ wall + 60s``."""

    def test_wall_clock_cap_is_600(self):
        from daemon.plugin_subsystem.opendesign import generate as gen_mod

        assert gen_mod._OD_GENERATE_WALL_CLOCK_CAP_S == _WALL_EXPECTED

    def test_dispatch_wait_constant_at_least_660(self):
        assert _COMPARATOR_DISPATCH_WAIT_S >= _WAIT_MINIMUM

    def test_invariant_shape_wall_lt_wait_strict_margin(self):
        assert _WALL_EXPECTED < _COMPARATOR_DISPATCH_WAIT_S
        assert _COMPARATOR_DISPATCH_WAIT_S >= _WALL_EXPECTED + 60.0

    def test_inner_attempt_advisory_below_wall(self):
        """Inner per-attempt timeout at the 200k budget ≈ 540 s < wall —
        ADVISORY only under streaming (5s heartbeats keep bytes flowing;
        the bound remains the per-attempt stall bound, plan §6.2)."""
        inner = max(120.0, DEFAULT_MAX_TOKENS / 370.0)
        assert inner < _WALL_EXPECTED


class TestMaxTokensDefaultsResolve:
    """Every live injection site resolves the SAME shared constant."""

    def test_shared_constant_is_200000(self):
        assert DEFAULT_MAX_TOKENS == 200000

    def test_dataclass_default_is_shared_constant(self):
        assert GenerateInput(prompt="x").max_tokens == DEFAULT_MAX_TOKENS

    def test_port_schema_default_is_shared_constant(self):
        ports, _ = validate_ports(declared_opendesign_ports())
        gen_port = next(p for p in ports if p.port_id == "od.generate")
        prop = gen_port.inputs_schema["properties"]["max_tokens"]
        assert prop["default"] == DEFAULT_MAX_TOKENS
        # The Port ceiling is unchanged: 200000 (the divisor input).
        assert prop["maximum"] == 200000

    def test_execute_dict_fallback_resolves_shared_constant(self, env):
        """(ii) ``execute_dict({})`` without the key → DEFAULT_MAX_TOKENS
        on the wire (the operative raw-dict fallback for the
        production StructuredTool→execute_dict path)."""
        captured: dict = {}

        def _capturing_invoker(**kwargs):
            captured.update(kwargs)
            return _complete_streamed_response()

        from daemon.plugin_subsystem.opendesign.generate import OdGenerate

        OdGenerate._set_test_hooks(llm_invoker=_capturing_invoker)
        try:
            result = OdGenerate.execute_dict({"prompt": "x"}, env=env)
        finally:
            from daemon.plugin_subsystem.opendesign import generate as gen_mod
            OdGenerate._set_test_hooks(llm_invoker=gen_mod._invoke_chat_via_facade)
        assert result["error"] is None
        assert captured["max_tokens"] == DEFAULT_MAX_TOKENS

    def test_execute_dict_invalid_values_clamp_to_shared_constant(
        self, env
    ):
        """(iv) ``max_tokens=0`` / ``"bogus"`` → DEFAULT_MAX_TOKENS,
        NOT the pre-Phase-2 64K value."""
        from daemon.plugin_subsystem.opendesign.generate import OdGenerate

        for bad in (0, "bogus", -5, 1000000):
            captured: dict = {}

            def _capturing_invoker(**kwargs):
                captured.update(kwargs)
                return _complete_streamed_response()

            OdGenerate._set_test_hooks(llm_invoker=_capturing_invoker)
            try:
                result = OdGenerate.execute_dict(
                    {"prompt": "x", "max_tokens": bad}, env=env
                )
            finally:
                from daemon.plugin_subsystem.opendesign import generate as gen_mod
                OdGenerate._set_test_hooks(
                    llm_invoker=gen_mod._invoke_chat_via_facade
                )
            assert result["error"] is None, f"bad={bad!r} must clamp, not fail"
            assert captured["max_tokens"] == DEFAULT_MAX_TOKENS, (
                f"max_tokens={bad!r} must clamp to DEFAULT_MAX_TOKENS "
                f"({DEFAULT_MAX_TOKENS})"
            )

    def test_production_tool_path_omitted_max_tokens_resolves_shared_constant(
        self, env, monkeypatch
    ):
        """(i) The StructuredTool production entry with ``max_tokens``
        omitted → DEFAULT_MAX_TOKENS effective. (The dict args_schema
        is not default-injected by langchain_core — the effective value
        comes from the execute_dict fallback; see module docstring.)"""
        from daemon.plugin_subsystem.opendesign.generate import OdGenerate

        tool = _generate_tool()
        captured: dict = {}

        def _capturing_invoker(**kwargs):
            captured.update(kwargs)
            return _complete_streamed_response()

        saved = OdGenerate._LLM_INVOKER
        OdGenerate._LLM_INVOKER = staticmethod(_capturing_invoker)
        try:
            result = tool.invoke({"prompt": "landing page", "kind": "prototype"})
        finally:
            OdGenerate._LLM_INVOKER = saved
        assert result["error"] is None
        assert captured["max_tokens"] == DEFAULT_MAX_TOKENS


class TestNoResidualLiteral:
    """No ``64000`` literal remains in generate.py / ports.py (§7.6)."""

    @pytest.mark.parametrize("rel_path", [_GENERATE_MOD, _PORTS_MOD])
    def test_no_64000_literal(self, rel_path):
        repo_root = Path(__file__).resolve().parents[3]
        source = (repo_root / rel_path).read_text(encoding="utf-8")
        assert "64000" not in source, (
            f"{rel_path} still carries a 64000 literal — the §6.2 "
            "budget chain must reference DEFAULT_MAX_TOKENS everywhere"
        )


def _complete_streamed_response():
    """Minimal ChatCompletion-shaped success for invoker-seam stubs."""
    from daemon.plugin_subsystem.opendesign.generate import StreamedChatCompletion

    class _Msg:
        content = "<!doctype html><html><head></head><body>OK</body></html>"
        reasoning_content = None

    class _Choice:
        finish_reason = "stop"
        message = _Msg()

    class _Usage:
        prompt_tokens = 1
        completion_tokens = 2
        total_tokens = 3
        completion_tokens_details = None

    class _Resp:
        choices = [_Choice()]
        usage = _Usage()

    return _Resp()
