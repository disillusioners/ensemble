"""LCA Stage-2 fused judge — output-cap regression matrix (F-A, 2026-09-16).

CI-registered regression suite for the fused-scoped output cap fix
introduced at :data:`daemon.services.attestation_report_judge.
FUSED_JUDGE_MAX_OUTPUT_CHARS` (default 2048). The fused prompt
(:data:`FUSED_JUDGE_SYSTEM_PROMPT`) mandates a payload shape —
5 × 120-char evidence + 240 advisory + 240 rationale — that
**exceeds the legacy cap** of 400 chars by construction. The
pre-fix shared cap silently downgraded every compliant verbose
verdict to unparsable ×2 (live probe caught what static review
could not); this suite pins the post-fix behavior end-to-end:

* **Case (a)** — compact 122-char verdict (verdict-only, no
  evidence). Pre-fix and post-fix identical: passes through the
  2048 cap unchanged, parses complete on attempt 1.
* **Case (b)** — realistic 997-char compliant verbose verdict
  (5 evidence_cited + advisory + rationale). Pre-fix FAILED
  (truncated at 400 → unparsable ×2 → ``is_complete=False``);
  post-fix PASSES (under 2048 cap → parses complete on attempt
  1). This is the LIVE-FAILURE case the tester flagged.
* **Case (c)** — 3072-char runaway verdict (3× the cap, mirrors
  a model that ran unconstrained). BOTH pre-fix and post-fix
  truncate, but the truncation now happens at 2048 instead of
  400 — the conservative fail-safe (``unparsable`` →
  ``is_complete=False``) is preserved either way. Pins that
  unbounded LLM output never reaches the parser.

Every case exercises the SAME transport seam
(:func:`daemon.services.attestation_report_judge.
judge_fused_bundle_async`) with a deterministic stub for
:func:`daemon.services.attestation_report_judge._invoke_judge_llm`,
so the suite is credentials-free and CI-fast.

History
-------
* **2026-09-16** — created during the F-A fix (fused-scoped cap
  raised from 400 → 2048 at the two fused sites only; legacy
  window judge keeps 400). Mirrors the layout of the
  manual-only probe at ``tests/probe/lca2_judge_truncation_repro.py``
  (``20024fda``); that probe was converted to flip its case-(b)
  expectation in the same commit.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import MagicMock

from daemon.services import attestation_report_judge as jm
from daemon.services.attestation_report_judge import (
    FUSED_JUDGE_MAX_OUTPUT_CHARS,
    judge_fused_bundle_async,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers (mirror tests/unit/test_attestation_fused_judge.py)
# ─────────────────────────────────────────────────────────────────────────────


def _config() -> MagicMock:
    cfg = MagicMock()
    cfg.llm.model_keywords = "quick"
    cfg.llm.request_timeout = 30.0
    return cfg


def _stub_factory(payload: str):
    """Deterministic LLM seam stub — returns ``payload`` on every call."""

    async def _stub(config, user_payload, *, timeout_s, system_prompt=None):
        return (payload, "fake-quick")

    return _stub


async def _run_case(payload: str) -> dict:
    """Run ONE case end-to-end and return a verdict-card dict."""
    original_fn = jm._invoke_judge_llm
    jm._invoke_judge_llm = _stub_factory(payload)
    try:
        result = await judge_fused_bundle_async(
            "<synthetic bundle — irrelevant for this matrix>",
            config=_config(),
        )
    finally:
        jm._invoke_judge_llm = original_fn

    return {
        "payload_chars": len(payload),
        "cap_chars": FUSED_JUDGE_MAX_OUTPUT_CHARS,
        "exceeds_cap": len(payload) > FUSED_JUDGE_MAX_OUTPUT_CHARS,
        "truncated_to": min(len(payload), FUSED_JUDGE_MAX_OUTPUT_CHARS),
        "verdict": result.verdict,
        "is_complete": result.is_complete,
        "attempt": result.attempt,
        "invoked": result.invoked,
        "first_unparsable_excerpt_len": (
            len(result.first_unparsable_excerpt)
            if result.first_unparsable_excerpt
            else 0
        ),
        "evidence_count": len(result.evidence_cited),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Payload fixtures — three sizes that bracket the fused cap.
# ─────────────────────────────────────────────────────────────────────────────


def _compact_payload() -> str:
    """~122-char compact verdict (verdict-only). Sits under both legacy
    (400) and fused (2048) caps; passes through unchanged on every
    pre/post-fix variant."""
    return json.dumps(
        {
            "verdict": "complete",
            "evidence_cited": ["report enumerates outcomes"],
            "advisory_note_text": "",
            "rationale": "genuine",
        }
    )


def _compliant_verbose_payload() -> str:
    """Realistic compliant verbose verdict (~997 chars). Mandated by
    :data:`FUSED_JUDGE_SYSTEM_PROMPT`: 5 evidence_cited entries +
    advisory + rationale. Exceeds the legacy 400-char cap; sits
    comfortably under the fused 2048-char cap. Pre-fix this case
    failed (truncated at 400 → unparsable ×2); post-fix it
    parses complete on attempt 1."""
    return json.dumps(
        {
            "verdict": "complete",
            "evidence_cited": [
                "Lead's final AIMessage enumerates per-child outcomes "
                "(merge ceb7694e on branch feature/lca-resolver-stage2).",
                "Tester RESULTS .agents/tester/RESULTS/2026-09-16-"
                "lca-stage2-verification.md covers Delta1-Delta4 shapes "
                "and DP-5 fail-safe-allow rejection.",
                "Governor architecture review APPROVE-WITH-CONDITIONS; "
                "four named conditions all satisfied in the shipped tree.",
                "Source C shows zero pending work and zero live "
                "descendants — no promised-but-undelivered follow-ups.",
                "Per-child attestations redacted-note-1..redacted-note-3 "
                "recorded in the context bus with stable-id supersede.",
            ],
            "advisory_note_text": (
                "Mission genuinely complete; no advisory needed for the "
                "lead — the evidence bundle has no contradicting notes."
            ),
            "rationale": (
                "Lead's final AIMessage is a genuine completion report: "
                "enumerates concrete deliverables (merge ceb7694e, RESULTS "
                "file, governor APPROVE-WITH-CONDITIONS), cites per-child "
                "attestations, and Source C shows zero live descendants."
            ),
        }
    )


def _runaway_payload() -> str:
    """3072-char runaway verdict (3x the fused cap). A model that ran
    unconstrained / echoed a long transcript. Truncation MUST still
    apply at the fused cap; unbounded LLM output never reaches the
    parser. After truncation, the slice is invalid JSON → retry
    fires → both attempts unparsable → conservative
    ``is_complete=False``. The conservative fail-safe is preserved."""
    pad = "x" * 3072
    # Realistic on-the-wire shape with a giant evidence entry.
    return json.dumps(
        {
            "verdict": "complete",
            "evidence_cited": [pad],
            "advisory_note_text": "",
            "rationale": "the model ran unconstrained",
        }
    )


# ─────────────────────────────────────────────────────────────────────────────
# Regression matrix
# ─────────────────────────────────────────────────────────────────────────────


class TestFusedJudgeOutputCapMatrix:
    """F-A regression — pinned post-fix outcomes for the three
    payload-size brackets."""

    def test_case_a_compact_payload_parses_complete_on_attempt_one(self):
        """(a) 122-char verdict fits under both caps; parses complete."""
        card = asyncio.run(_run_case(_compact_payload()))
        assert card["payload_chars"] < FUSED_JUDGE_MAX_OUTPUT_CHARS
        assert card["exceeds_cap"] is False
        assert card["truncated_to"] == card["payload_chars"]
        assert card["verdict"] == "complete"
        assert card["is_complete"] is True
        assert card["attempt"] == 1
        assert card["invoked"] is True
        assert card["first_unparsable_excerpt_len"] == 0
        assert card["evidence_count"] == 1

    def test_case_b_compliant_verbose_payload_parses_complete_on_attempt_one(
        self,
    ):
        """(b) ~997-char compliant verbose verdict — THIS WAS THE LIVE
        FAILURE. Pre-fix truncated at 400 → unparsable ×2 →
        ``is_complete=False``; post-fix parses complete on attempt 1.

        The test pins that the fused-scoped cap (2048) accommodates the
        fused prompt's mandated payload shape, so a compliant verbose
        verdict reaches the parser unmangled."""
        payload = _compliant_verbose_payload()
        card = asyncio.run(_run_case(payload))
        assert card["payload_chars"] > 400, (
            "Case (b) fixture must exceed the LEGACY 400 cap to be a "
            "meaningful regression — otherwise the test passes trivially "
            "even without the fused-scoped fix."
        )
        assert card["payload_chars"] <= FUSED_JUDGE_MAX_OUTPUT_CHARS, (
            "Case (b) fixture must fit under the FUSED cap to exercise "
            "the post-fix happy path."
        )
        assert card["exceeds_cap"] is False
        assert card["truncated_to"] == card["payload_chars"]
        assert card["verdict"] == "complete"
        assert card["is_complete"] is True
        assert card["attempt"] == 1
        assert card["invoked"] is True
        assert card["first_unparsable_excerpt_len"] == 0
        assert card["evidence_count"] == 5, (
            "Case (b) fixture carries 5 evidence_cited entries — all "
            "five must survive parsing intact."
        )

    def test_case_c_runaway_payload_truncated_conservative_fail_safe(self):
        """(c) 3072-char payload (3x the fused cap) — truncation still
        applies, conservative fail-safe preserved. The slice at 2048
        is invalid JSON → retry fires → both attempts unparsable →
        ``is_complete=False``.

        Pins that unbounded LLM output never reaches the parser
        unmangled AND that the conservative mapping
        (``unparsable`` → ``is_complete=False``) survives at the new
        cap level. Distinct from pre-fix: the cap moved 400 → 2048,
        so a payload that previously would have been DOUBLE-truncated
        (twice unparsable) is still double-truncated post-fix, but at
        a higher slice — the contract is the conservative outcome,
        not the truncation point."""
        payload = _runaway_payload()
        card = asyncio.run(_run_case(payload))
        assert card["payload_chars"] > FUSED_JUDGE_MAX_OUTPUT_CHARS
        assert card["exceeds_cap"] is True
        assert card["truncated_to"] == FUSED_JUDGE_MAX_OUTPUT_CHARS
        # Conservative fail-safe: unparsable ×2 → is_complete=False.
        assert card["verdict"] == "unparsable"
        assert card["is_complete"] is False
        assert card["attempt"] == 2
        assert card["invoked"] is True
        assert card["first_unparsable_excerpt_len"] > 0


# ─────────────────────────────────────────────────────────────────────────────
# Drift pin — fused cap is exactly 2048 (the constant the F-A fix
# landed). If a future change silently re-shares the cap with the
# legacy judge, this test goes loud.
# ─────────────────────────────────────────────────────────────────────────────


class TestFusedCapDriftPin:
    """Stage 3 (2026-09-17, R7): the legacy window judge and its
    400-char cap are DELETED — the fused cap is the ONLY output cap.
    The drift pins now guard the singleton-cap contract."""

    def test_fused_cap_is_2048(self):
        # F-A introduced the fused-scoped cap; Stage 3 deleted the
        # legacy twin. The fused cap is the sole survivor.
        assert FUSED_JUDGE_MAX_OUTPUT_CHARS == 2048

    def test_legacy_cap_constant_deleted(self):
        assert not hasattr(jm, "JUDGE_MAX_OUTPUT_CHARS")

    def test_fused_sites_use_fused_cap(self):
        # Defensive pin — the fused truncation sites (judge fused
        # bundle async, lines 1340-1341 / 1378-1379) MUST use the
        # fused cap, not the legacy one. If a future refactor swaps
        # them back, the F-A regression reopens.
        import inspect

        src = inspect.getsource(jm.judge_fused_bundle_async)
        assert "FUSED_JUDGE_MAX_OUTPUT_CHARS" in src
        # The fused function should NOT reference the legacy cap
        # anywhere in its source.
        legacy_ref_count = src.count("JUDGE_MAX_OUTPUT_CHARS")
        fused_ref_count = src.count("FUSED_JUDGE_MAX_OUTPUT_CHARS")
        # Only the FUSED_JUDGE_MAX_OUTPUT_CHARS prefix is allowed;
        # the bare JUDGE_MAX_OUTPUT_CHARS token must be absent.
        assert legacy_ref_count == fused_ref_count, (
            f"Fused judge references both caps — legacy={legacy_ref_count}, "
            f"fused={fused_ref_count}. Each must appear exactly once."
        )
        # And specifically: the legacy token never appears unprefixed.
        import re

        bare_legacy = re.findall(r"(?<!FUSED_)JUDGE_MAX_OUTPUT_CHARS", src)
        assert bare_legacy == [], (
            f"Fused judge still references the legacy cap: {bare_legacy}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# (Any:) tiny compile-check — importing the module exercises the
# constant definitions so a typo at the module level surfaces as a
# collection failure rather than a runtime surprise.
# ─────────────────────────────────────────────────────────────────────────────


def test_module_constants_resolve():
    assert isinstance(FUSED_JUDGE_MAX_OUTPUT_CHARS, int)
    # Stage 3 (R7): the legacy 400-char cap constant is deleted —
    # the fused cap is the only output cap.
    assert not hasattr(jm, "JUDGE_MAX_OUTPUT_CHARS")
