"""Phase-3 bounded retry-on-truncation tests (plan od-generate-async-poll §6.3, §7.10).

``OdGenerate.execute`` re-attempts the SAME prompt EXACTLY ONCE when
the Gate-2 completeness gate refuses with ``finish_reason="length"``
(the model consumed its whole budget before closing — the Phase-0
probe's thinking-only profile truncates exactly this way). A second
truncation fails typed with NO third attempt. Deterministic refusals
(Gate-1 empty, Gate-3 missing marker, Gate-2 with a non-"length"
reason such as content_filter) are never re-attempted.

Offline-first: the ``_LLM_INVOKER`` seam carries scripted responses;
no network, no facade.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from daemon.plugin_subsystem.opendesign.generate import (
    GenerateInput,
    OdGenerate,
)

COMPLETE_HTML = "<!doctype html><html><head></head><body>OK</body></html>"

ENV = {
    "OPENAI_BASE_URL": "http://fake.test/v1",
    "OPENAI_API_KEY": "fake-key",
    "OPENAI_MODEL_VISION": "vision",
}


def _response(
    finish_reason: str,
    content: str,
    *,
    prompt_tokens: int = 198,
    completion_tokens: int = 8000,
) -> Any:
    """Minimal ChatCompletion-shaped buffered response (invoker seam)."""
    msg = type("M", (), {"content": content})()
    choice = type("C", (), {"finish_reason": finish_reason, "message": msg})()
    usage = type(
        "U",
        (),
        {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "completion_tokens_details": None,
        },
    )()
    return type("R", (), {"choices": [choice], "usage": usage})()


class _ScriptedInvoker:
    """Scripted side-effect queue capturing every call's kwargs."""

    def __init__(self, responses: List[Any]):
        self._responses = list(responses)
        self.calls: List[Dict[str, Any]] = []

    def __call__(self, **kwargs):
        self.calls.append(dict(kwargs))
        if not self._responses:
            raise AssertionError("script exhausted — more LLM calls than scripted")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def scripted():
    def _install(responses):
        invoker = _ScriptedInvoker(responses)
        OdGenerate._set_test_hooks(llm_invoker=invoker)
        return invoker

    yield _install
    from daemon.plugin_subsystem.opendesign import generate as gen_mod
    OdGenerate._set_test_hooks(llm_invoker=gen_mod._invoke_chat_via_facade)


class TestExactlyOneBound:
    def test_truncation_then_success_reattempts_exactly_once(self, scripted):
        """§7.10: first ``finish_reason="length"`` (the probe's
        thinking-only profile — ZERO answer chars) triggers ONE
        same-prompt re-attempt; the second call succeeds. Invoker call
        count == 2 and the SAME prompt rides both calls."""
        invoker = scripted(
            [
                _response("length", "", completion_tokens=8000),
                _response("stop", COMPLETE_HTML, completion_tokens=9000),
            ]
        )
        result = OdGenerate.execute(
            GenerateInput(prompt="landing page", kind="prototype"), env=ENV
        )
        assert len(invoker.calls) == 2, (
            f"exactly ONE re-attempt expected; saw {len(invoker.calls)} calls"
        )
        assert result["error"] is None
        assert result["html"] == COMPLETE_HTML
        assert result["truncated"] is False
        # Same prompt on both attempts (re-attempt is same-prompt).
        assert invoker.calls[0]["user_prompt"] == invoker.calls[1]["user_prompt"]
        assert invoker.calls[0]["system_prompt"] == invoker.calls[1]["system_prompt"]
        assert invoker.calls[0]["max_tokens"] == invoker.calls[1]["max_tokens"]

    def test_second_truncation_fails_typed_no_third_attempt(self, scripted):
        """§7.10: a SECOND truncation fails typed — NO third attempt.
        Invoker call count == exactly 2 (1 + the single bound)."""
        invoker = scripted(
            [
                _response("length", "<html><body>partial-1"),
                _response("length", "<html><body>partial-2"),
            ]
        )
        result = OdGenerate.execute(
            GenerateInput(prompt="x", kind="prototype"), env=ENV
        )
        assert len(invoker.calls) == 2, (
            f"no third attempt may fire; saw {len(invoker.calls)} calls"
        )
        assert result["error"]["ok"] is False
        assert result["error"]["code"] == "truncation_detected"
        assert result["truncated"] is True
        assert result["finish_reason"] == "length"


class TestDeterministicRefusalsNeverReattempt:
    def test_empty_response_gate1_no_reattempt(self, scripted):
        invoker = scripted([_response("stop", "")])
        result = OdGenerate.execute(
            GenerateInput(prompt="x", kind="prototype"), env=ENV
        )
        assert len(invoker.calls) == 1, "Gate-1 empty is deterministic — no re-attempt"
        assert result["error"]["code"] == "empty_response"

    def test_missing_artifact_marker_gate3_no_reattempt(self, scripted):
        invoker = scripted([_response("stop", "<html><body>no close")])
        result = OdGenerate.execute(
            GenerateInput(prompt="x", kind="prototype"), env=ENV
        )
        assert len(invoker.calls) == 1, "Gate-3 marker is deterministic — no re-attempt"
        assert result["error"]["code"] == "missing_artifact_marker"

    def test_content_filter_gate2_non_length_no_reattempt(self, scripted):
        invoker = scripted([_response("content_filter", COMPLETE_HTML)])
        result = OdGenerate.execute(
            GenerateInput(prompt="x", kind="prototype"), env=ENV
        )
        assert len(invoker.calls) == 1, (
            "Gate-2 with a non-length reason is deterministic — no re-attempt"
        )
        assert result["error"]["code"] == "truncation_detected"
        assert result["finish_reason"] == "content_filter"
