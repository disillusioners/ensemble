"""LCA unified resolver — Stage-2 fused judge unit tests (2026-09-16).

Covers :func:`daemon.services.attestation_report_judge.
judge_fused_bundle_async` — the ONE judge call site of the flipped
resolver (graph-node fused block):

* Verdict-JSON parsing (:func:`_parse_fused_judge_response`) — the
  strict/tolerant shape mirrors the legacy judge's parser; the
  load-bearing field is ``verdict`` ("complete"|"not_complete").
* Retry semantics (incident 98b59dd7 lineage + incident bc145c7e
  R1, 2026-09-19): the retry fires on EITHER attempt-1
  ``asyncio.TimeoutError`` (the bc145c7e R1 supersession of the prior
  no-timeout-retry decision for the rescuer path; quick-model tail
  latency makes the class recurring) OR attempt-1 unparsable verdict
  JSON (the preserved 98b59dd7 contract carried over from the retired
  legacy judge). HTTP / API errors keep NO retry (unchanged). On
  EITHER retry path the result carries ``attempt=2``;
  ``first_unparsable_excerpt`` is set ONLY on the unparsable-retry
  path (a timeout leaves no body to redact/excerpt).
* The ``invoked`` real-invocation flag (Stage-1 review hazard pin:
  the eval row's ``judge_invoked`` derives from it) — True on every
  LLM-attempt path, False ONLY on the degenerate empty-bundle guard.
* Transport invariants SHARED with the legacy judge via the single
  ``_invoke_judge_llm`` seam: the fused call passes its own system
  prompt; the legacy default prompt is untouched (backward pin).
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest

from daemon.services import attestation_report_judge as jm
from daemon.services.attestation_report_judge import (
    FUSED_JUDGE_SYSTEM_PROMPT,
    FusedJudgeResult,
    judge_fused_bundle_async,
)


def _config() -> MagicMock:
    cfg = MagicMock()
    cfg.llm.model_keywords = "quick"
    cfg.llm.request_timeout = 30.0
    return cfg


def _complete_payload() -> str:
    return (
        '{"verdict": "complete", "evidence_cited": ["report enumerates outcomes"], '
        '"advisory_note_text": "", "rationale": "genuine"}'
    )


def _not_complete_payload() -> str:
    return (
        '{"verdict": "not_complete", "evidence_cited": ["child promised future work"], '
        '"advisory_note_text": "Check the child", "rationale": "contradiction"}'
    )


# ─────────────────────────────────────────────────────────────────────────────
# Parser
# ─────────────────────────────────────────────────────────────────────────────


class TestParseFusedJudgeResponse:
    def test_complete_verdict(self):
        parsed = jm._parse_fused_judge_response(_complete_payload())
        assert parsed is not None
        is_complete, evidence, advisory, rationale = parsed
        assert is_complete is True
        assert evidence == ("report enumerates outcomes",)
        assert advisory == ""
        assert rationale == "genuine"

    def test_not_complete_verdict(self):
        parsed = jm._parse_fused_judge_response(_not_complete_payload())
        assert parsed is not None
        is_complete, evidence, advisory, _ = parsed
        assert is_complete is False
        assert evidence == ("child promised future work",)
        assert advisory == "Check the child"

    def test_missing_optional_fields_tolerated(self):
        parsed = jm._parse_fused_judge_response('{"verdict": "complete"}')
        assert parsed == (True, (), "", "")

    def test_wrong_verdict_value_is_unparsable(self):
        assert jm._parse_fused_judge_response('{"verdict": "maybe"}') is None
        assert jm._parse_fused_judge_response('{"verdict": null}') is None
        assert jm._parse_fused_judge_response("{}") is None

    def test_legacy_shape_is_unparsable(self):
        # The legacy {"is_complete_report": bool} shape does NOT parse —
        # the fused contract is the verdict-JSON only.
        assert (
            jm._parse_fused_judge_response(
                '{"is_complete_report": true, "reason": "x"}'
            )
            is None
        )

    def test_prose_is_unparsable(self):
        assert jm._parse_fused_judge_response("Sure, here you go.") is None
        assert jm._parse_fused_judge_response("") is None

    def test_code_fence_tolerated(self):
        fenced = f"```json\n{_not_complete_payload()}\n```"
        parsed = jm._parse_fused_judge_response(fenced)
        assert parsed is not None
        assert parsed[0] is False

    def test_evidence_caps(self):
        items = [f"quote {i}" for i in range(10)]
        long_item = "x" * 500
        payload = json.dumps(
            {
                "verdict": "not_complete",
                "evidence_cited": [*items, long_item],
            }
        )
        _, evidence, _, _ = jm._parse_fused_judge_response(payload)
        assert len(evidence) == jm.FUSED_JUDGE_EVIDENCE_ITEMS_MAX
        assert all(
            len(item) <= jm.FUSED_JUDGE_EVIDENCE_ITEM_MAX_CHARS
            for item in evidence
        )

    def test_advisory_capped(self):
        payload = json.dumps(
            {"verdict": "not_complete", "advisory_note_text": "a" * 500}
        )
        _, _, advisory, _ = jm._parse_fused_judge_response(payload)
        assert len(advisory) <= jm.FUSED_JUDGE_ADVISORY_MAX_CHARS


# ─────────────────────────────────────────────────────────────────────────────
# judge_fused_bundle_async — verdict paths + retry semantics
# ─────────────────────────────────────────────────────────────────────────────


class TestFusedJudgeVerdictPaths:
    def test_complete(self, monkeypatch):
        async def _stub(config, payload, *, timeout_s, system_prompt=None):
            return (_complete_payload(), "fake-quick")

        monkeypatch.setattr(jm, "_invoke_judge_llm", _stub)
        result = asyncio.run(
            judge_fused_bundle_async("bundle text", config=_config())
        )
        assert isinstance(result, FusedJudgeResult)
        assert result.invoked is True
        assert result.is_complete is True
        assert result.verdict == "complete"
        assert result.attempt == 1
        assert result.error_class is None
        # advisory cleared on complete verdicts (no hint fires on allow)
        assert result.advisory_note_text == ""

    def test_not_complete_carries_evidence(self, monkeypatch):
        async def _stub(config, payload, *, timeout_s, system_prompt=None):
            return (_not_complete_payload(), "fake-quick")

        monkeypatch.setattr(jm, "_invoke_judge_llm", _stub)
        result = asyncio.run(
            judge_fused_bundle_async("bundle text", config=_config())
        )
        assert result.is_complete is False
        assert result.verdict == "not_complete"
        assert result.evidence_cited == ("child promised future work",)
        assert result.advisory_note_text == "Check the child"

    def test_timeout_retries_once_recovered(self, monkeypatch):
        # incident bc145c7e R1 (2026-09-19): the rescuer judge retries
        # ONCE on attempt-1 timeout. Successful retry → verdict
        # honored; first-attempt timeout is consumed (no excerpt, no
        # unparsable row — the prior attempt left no body).
        responses = [None, _complete_payload()]
        calls = []

        async def _stub(config, payload, *, timeout_s, system_prompt=None):
            calls.append(payload)
            if responses[len(calls) - 1] is None:
                raise asyncio.TimeoutError()
            return (responses[len(calls) - 1], "fake-quick")

        monkeypatch.setattr(jm, "_invoke_judge_llm", _stub)
        result = asyncio.run(
            judge_fused_bundle_async("bundle", config=_config())
        )
        assert len(calls) == 2
        assert result.attempt == 2
        assert result.verdict == "complete"
        assert result.is_complete is True
        # No first_unparsable_excerpt on a timeout-retry path — the
        # timeout left no body to redact/excerpt.
        assert result.first_unparsable_excerpt is None

    def test_timeout_post_retry_conservative_deny(self, monkeypatch):
        # bc145c7e R1: post-retry timeout (both attempts timed out) →
        # conservative fail-safe deny. DP-5 posture unchanged.
        calls = []

        async def _stub(config, payload, *, timeout_s, system_prompt=None):
            calls.append(1)
            raise asyncio.TimeoutError()

        monkeypatch.setattr(jm, "_invoke_judge_llm", _stub)
        result = asyncio.run(
            judge_fused_bundle_async("bundle", config=_config())
        )
        # ONE retry — exactly 2 HTTP attempts within ONE invocation.
        assert len(calls) == 2
        assert result.attempt == 2
        assert result.verdict == "timeout"
        assert result.is_complete is False
        assert result.error_class == "TimeoutError"

    def test_timeout_then_error_post_retry_conservative(self, monkeypatch):
        # bc145c7e R1: timeout@1 → error@2 → conservative deny.
        # attempt=2, verdict=error, error_class set.
        state = {"n": 0}

        async def _stub(config, payload, *, timeout_s, system_prompt=None):
            state["n"] += 1
            if state["n"] == 1:
                raise asyncio.TimeoutError()
            raise RuntimeError("boom")

        monkeypatch.setattr(jm, "_invoke_judge_llm", _stub)
        result = asyncio.run(
            judge_fused_bundle_async("bundle", config=_config())
        )
        assert result.attempt == 2
        assert result.verdict == "error"
        assert result.error_class == "RuntimeError"
        assert result.is_complete is False

    def test_timeout_retry_log_discrimination(self, monkeypatch, caplog):
        # bc145c7e R1: log rows carry the discrimination tokens for the
        # timeout-retry paths. Pin the exact token strings so grep /
        # incident triage can rely on them.
        import logging

        calls = {"n": 0}

        async def _stub(config, payload, *, timeout_s, system_prompt=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise asyncio.TimeoutError()
            return (_complete_payload(), "fake-quick")

        monkeypatch.setattr(jm, "_invoke_judge_llm", _stub)
        with caplog.at_level(
            logging.INFO, logger="daemon.services.attestation_report_judge"
        ):
            result = asyncio.run(
                judge_fused_bundle_async("bundle", config=_config())
            )
        assert result.verdict == "complete"
        text = caplog.text
        assert "event=fused_judge_first_attempt_timeout" in text
        assert "event=fused_judge_timeout_retry" in text
        # Post-retry token must NOT appear on a recovered retry.
        assert "event=fused_judge_timeout_post_retry" not in text

    def test_timeout_post_retry_log_discrimination(self, monkeypatch, caplog):
        # bc145c7e R1: post-retry timeout logs BOTH the first-attempt
        # token AND the post-retry token; the retry token MUST appear
        # (the retry was actually fired).
        import logging

        async def _stub(config, payload, *, timeout_s, system_prompt=None):
            raise asyncio.TimeoutError()

        monkeypatch.setattr(jm, "_invoke_judge_llm", _stub)
        with caplog.at_level(
            logging.INFO, logger="daemon.services.attestation_report_judge"
        ):
            result = asyncio.run(
                judge_fused_bundle_async("bundle", config=_config())
            )
        assert result.verdict == "timeout"
        text = caplog.text
        assert "event=fused_judge_first_attempt_timeout" in text
        assert "event=fused_judge_timeout_retry" in text
        assert "event=fused_judge_timeout_post_retry" in text

    def test_error_no_retry_conservative(self, monkeypatch):
        calls = []

        async def _stub(config, payload, *, timeout_s, system_prompt=None):
            calls.append(1)
            raise RuntimeError("boom")

        monkeypatch.setattr(jm, "_invoke_judge_llm", _stub)
        result = asyncio.run(
            judge_fused_bundle_async("bundle text", config=_config())
        )
        assert result.verdict == "error"
        assert result.is_complete is False
        assert result.error_class == "RuntimeError"
        assert len(calls) == 1

    def test_empty_bundle_guard_invoked_false(self):
        result = asyncio.run(
            judge_fused_bundle_async("", config=_config())
        )
        assert result.invoked is False
        assert result.verdict == "error"
        assert result.error_class == "EmptyBundle"

    def test_never_raises_on_wrapper_stub_explosion(self, monkeypatch):
        async def _boom(*args, **kwargs):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(jm, "_invoke_judge_llm", _boom)
        result = asyncio.run(
            judge_fused_bundle_async("bundle", config=_config())
        )
        # never-raises contract: error verdict, conservative.
        assert result.verdict == "error"
        assert result.is_complete is False


class TestFusedJudgeRetryOnceOnUnparsable:
    def test_retry_recovers(self, monkeypatch):
        responses = ["prose, no JSON", _not_complete_payload()]
        calls = []

        async def _stub(config, payload, *, timeout_s, system_prompt=None):
            calls.append(payload)
            return (responses[len(calls) - 1], "fake-quick")

        monkeypatch.setattr(jm, "_invoke_judge_llm", _stub)
        result = asyncio.run(
            judge_fused_bundle_async("bundle", config=_config())
        )
        assert len(calls) == 2
        assert result.attempt == 2
        assert result.verdict == "not_complete"
        assert result.first_unparsable_excerpt  # forensic excerpt preserved

    def test_both_unparsable_conservative(self, monkeypatch):
        calls = []

        async def _stub(config, payload, *, timeout_s, system_prompt=None):
            calls.append(1)
            return ("Sorry, cannot help.", "fake-quick")

        monkeypatch.setattr(jm, "_invoke_judge_llm", _stub)
        result = asyncio.run(
            judge_fused_bundle_async("bundle", config=_config())
        )
        assert len(calls) == 2  # exactly ONE retry — never a third
        assert result.verdict == "unparsable"
        assert result.is_complete is False
        assert result.attempt == 2
        assert result.first_unparsable_excerpt

    def test_retry_timeout_carries_attempt2(self, monkeypatch):
        state = {"n": 0}

        async def _stub(config, payload, *, timeout_s, system_prompt=None):
            state["n"] += 1
            if state["n"] == 1:
                return ("prose", "fake-quick")
            raise asyncio.TimeoutError()

        monkeypatch.setattr(jm, "_invoke_judge_llm", _stub)
        result = asyncio.run(
            judge_fused_bundle_async("bundle", config=_config())
        )
        assert result.verdict == "timeout"
        assert result.attempt == 2
        assert result.first_unparsable_excerpt


# ─────────────────────────────────────────────────────────────────────────────
# Shared transport seam pins
# ─────────────────────────────────────────────────────────────────────────────


class TestSharedTransportSeam:
    def test_fused_call_passes_its_own_system_prompt(self, monkeypatch):
        seen = {}

        async def _stub(config, payload, *, timeout_s, system_prompt=None):
            seen["prompt"] = system_prompt
            seen["payload"] = payload
            return (_complete_payload(), "fake-quick")

        monkeypatch.setattr(jm, "_invoke_judge_llm", _stub)
        asyncio.run(
            judge_fused_bundle_async("[LCA FUSED EVIDENCE BUNDLE v1]", config=_config())
        )
        assert seen["prompt"] == FUSED_JUDGE_SYSTEM_PROMPT
        # The bundle is the payload VERBATIM (no re-truncation).
        assert seen["payload"] == "[LCA FUSED EVIDENCE BUNDLE v1]"

    def test_system_prompt_is_required_kwarg(self):
        # Stage 3 (R7): the legacy window judge and its prompt constant
        # are deleted — ``system_prompt`` is now a REQUIRED keyword on
        # the shared LLM seam (the fused judge passes
        # FUSED_JUDGE_SYSTEM_PROMPT explicitly).
        import inspect

        sig = inspect.signature(jm._invoke_judge_llm)
        assert sig.parameters["system_prompt"].default is inspect.Parameter.empty

    def test_fused_prompt_names_all_three_sources(self):
        # The prompt must orient the judge to the bundle's three
        # sections so evidence_cited can reference them.
        assert "SOURCE A" in FUSED_JUDGE_SYSTEM_PROMPT
        assert "SOURCE B" in FUSED_JUDGE_SYSTEM_PROMPT
        assert "SOURCE C" in FUSED_JUDGE_SYSTEM_PROMPT
        assert "not_complete" in FUSED_JUDGE_SYSTEM_PROMPT
