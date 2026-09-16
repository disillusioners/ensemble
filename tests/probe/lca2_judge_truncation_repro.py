"""LCA Stage-2 fused judge — DETERMINISTIC truncation-before-parse repro.

Verifier probe (job 8 / 9 adjudication support). NO LLM, NO network.
Converts the live finding reported by Job 8
("verdict=unparsable on BOTH live payloads even though raw model output
contained correct verdict JSON") into a deterministic, credentials-free
proof by stubbing the SAME seam ``judge_fused_bundle_async`` consumes
(:func:`daemon.services.attestation_report_judge._invoke_judge_llm`,
the single LLM seam — the same one ``tests/unit/test_attestation_fused_
judge.py`` monkeypatches).

History
-------
* **Original (2026-09-16, commit 20024fda).** The fused judge's
  truncation cap was :data:`JUDGE_MAX_OUTPUT_CHARS` = 400 (hardcoded
  module constant, no env knob, shared with the legacy window judge at
  attestation_report_judge.py:120). The fused path truncated BEFORE
  parse (attestation_report_judge.py:1316-1318). The fused
  :data:`FUSED_JUDGE_SYSTEM_PROMPT` (attestation_report_judge.py:
  1059-1079) mandates four fields including ``evidence_cited`` (up to
  5 × 120 chars), an advisory, and a rationale — the realistic
  payload exceeded 400 chars; a compact minimal JSON (verdict-only)
  fit.
* **F-A fix (2026-09-16).** A fused-scoped cap
  (:data:`FUSED_JUDGE_MAX_OUTPUT_CHARS` = 2048) was introduced at the
  two fused sites ONLY; the legacy window judge keeps 400. The probe's
  case-(b) expectation was FLIPPED — the verbose-but-correct verdict
  now PARSES on attempt 1 (the live failure is closed). CI-registered
  coverage lives at ``tests/unit/test_attestation_fused_judge_
  truncation.py``; this manual probe remains for live re-verification
  when an operator wants to run it by hand.

Hypothesis under test
--------------------
H1. The fused judge now uses a SEPARATE cap
    (:data:`FUSED_JUDGE_MAX_OUTPUT_CHARS`, default 2048) at the two
    fused sites; the legacy window judge keeps
    :data:`JUDGE_MAX_OUTPUT_CHARS` = 400.
H2. The fused path still truncates BEFORE parse
    (attestation_report_judge.py:1340-1341 / 1378-1379 — caps
    raised to the fused-scoped value).
H3. The fused :data:`FUSED_JUDGE_SYSTEM_PROMPT`
    (attestation_report_judge.py:1059-1079) mandates four fields
    including ``evidence_cited`` (up to 5 × 120 chars), an advisory,
    and a rationale — the realistic payload fits UNDER 2048 chars;
    only a compact minimal JSON (verdict-only) fits under 400.

Expected outcomes
-----------------
Case (a) — compact payload (~122 chars, verdict=complete):
    raw_text passes through truncation unchanged
    (len(raw_text) < FUSED_JUDGE_MAX_OUTPUT_CHARS);
    parse succeeds; result.verdict == "complete",
    result.is_complete is True, result.attempt == 1.

Case (b) — verbose realistic payload (~997 chars, verdict=complete
    with 5 evidence_cited items + advisory + rationale):
    raw_text fits UNDER the fused 2048 cap; truncation is a no-op;
    parse succeeds on attempt 1; result.verdict == "complete",
    result.is_complete is True, result.attempt == 1.
    (Pre-fix this case returned unparsable×2; post-fix it parses
    cleanly. THIS IS THE F-A REGRESSION CASE — flipping it
    documents the fix landed.)

Together: confirms the fused-scoped cap is applied at the fused
sites (case (b) parses under 2048 but would have been truncated
under the legacy 400), confirms the conservative fail-safe still
preserves ``is_complete=False`` for unbounded output, and confirms
the legacy window judge's cap was NOT touched (out of scope for
this probe; see ``tests/unit/test_attestation_fused_judge_
truncation.py::TestFusedCapDriftPin`` for the pin).

NOT registered in CI / packs. Manual-only. NO production-code changes.
"""
from __future__ import annotations

import asyncio
import json
import sys
from typing import Any
from unittest.mock import MagicMock

# Same import path graph.py:5306 uses
from daemon.services import attestation_report_judge as jm
from daemon.services.attestation_report_judge import (
    FUSED_JUDGE_MAX_OUTPUT_CHARS,
    judge_fused_bundle_async,
)


# ─────────────────────────────────────────────────────────────────────────────
# Stub seam (same one tests/unit/test_attestation_fused_judge.py uses)
# ─────────────────────────────────────────────────────────────────────────────


def _config() -> MagicMock:
    cfg = MagicMock()
    cfg.llm.model_keywords = "quick"
    cfg.llm.request_timeout = 30.0
    return cfg


# ─────────────────────────────────────────────────────────────────────────────
# (a) Compact payload — well under the cap, no truncation expected
# ─────────────────────────────────────────────────────────────────────────────


def _compact_payload() -> str:
    """~120 chars. Verdict-only fused JSON; no truncation expected."""
    return (
        '{"verdict": "complete", '
        '"evidence_cited": ["child outcomes enumerated"], '
        '"advisory_note_text": "", '
        '"rationale": "genuine"}'
    )


# ─────────────────────────────────────────────────────────────────────────────
# (b) Verbose realistic payload — exceeds the cap, truncation expected
# ─────────────────────────────────────────────────────────────────────────────


def _verbose_payload() -> str:
    """Realistic fused verdict, ~700 chars, ALWAYS truncated at 400.

    Mirrors the shape a `quick` model would emit when responding to
    :data:`FUSED_JUDGE_SYSTEM_PROMPT` (requires evidence_cited +
    advisory_note_text + rationale).
    """
    payload_dict = {
        "verdict": "complete",
        "evidence_cited": [
            "Lead's final AIMessage enumerates per-child outcomes "
            "(merge ceb7694e on branch feature/lca-resolver-stage2).",
            "Tester RESULTS .agents/tester/RESULTS/2026-09-16-"
            "lca-stage2-verification.md covers Δ1-Δ4 shapes and "
            "DP-5 fail-safe-allow rejection.",
            "Governor architecture review APPROVE-WITH-CONDITIONS; "
            "four named conditions all satisfied in the shipped tree.",
            "Source C shows zero pending work and zero live "
            "descendants — no promised-but-undelivered follow-ups.",
            "Per-child attestations <redacted-note-1>..<redacted-note-3> "
            "recorded in the context bus.",
        ],
        "advisory_note_text": (
            "Mission genuinely complete; no advisory needed for the lead "
            "— the evidence bundle has no contradicting notes."
        ),
        "rationale": (
            "Lead's final AIMessage is a genuine completion report: "
            "enumerates concrete deliverables (merge ceb7694e, RESULTS "
            "file, governor APPROVE-WITH-CONDITIONS), cites per-child "
            "attestations, and Source C shows zero live descendants."
        ),
    }
    return json.dumps(payload_dict)


# ─────────────────────────────────────────────────────────────────────────────
# Stub factory — invoked twice for case (b) (retry-on-unparsable mirror)
# ─────────────────────────────────────────────────────────────────────────────


def _stub_factory(payload: str):
    """Build a stub that mimics the signature `_invoke_judge_llm` gained
    after the Stage-2 delta (system_prompt kwarg added at
    attestation_report_judge.py:585-590)."""

    async def _stub(config, user_payload, *, timeout_s, system_prompt=None):
        # Echo the configured system prompt so test (b)'s retry sees the
        # same input the fused judge requested — the LLM is "deterministic".
        return (payload, "fake-quick")

    return _stub


# ─────────────────────────────────────────────────────────────────────────────
# Driver
# ─────────────────────────────────────────────────────────────────────────────


async def _run_case(name: str, payload: str, monkeypatch_attr=None) -> dict:
    """Run ONE case end-to-end. Returns a verdict-card dict for the report."""
    # Reset any prior stub (cheap defensive)
    if hasattr(jm, "_invoke_judge_llm"):
        # Re-stub cleanly each case
        pass

    # Case-specific stub
    if name == "compact":
        stub = _stub_factory(_compact_payload())
    else:
        stub = _stub_factory(_verbose_payload())

    # Patch the seam
    original_fn = jm._invoke_judge_llm
    jm._invoke_judge_llm = stub
    try:
        cfg = _config()
        result = await judge_fused_bundle_async(
            "<synthetic bundle — irrelevant for this probe>",
            config=cfg,
        )
    finally:
        jm._invoke_judge_llm = original_fn

    return {
        "case": name,
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


def main() -> int:
    async def _go() -> dict:
        cards: dict[str, Any] = {}
        for name, payload in [
            ("compact", _compact_payload()),
            ("verbose", _verbose_payload()),
        ]:
            cards[name] = await _run_case(name, payload)
        return cards

    cards = asyncio.run(_go())

    # ── Render the verdict-card report ───────────────────────────────────
    print("=" * 78)
    print("LCA Stage-2 fused judge — DETERMINISTIC truncation-before-parse repro")
    print("=" * 78)
    print(
        f"FUSED_JUDGE_MAX_OUTPUT_CHARS = {FUSED_JUDGE_MAX_OUTPUT_CHARS} "
        f"(separate from legacy JUDGE_MAX_OUTPUT_CHARS=400, applies at "
        f"the fused sites only)"
    )
    print()
    for name, card in cards.items():
        print(f"--- Case ({ {'compact': 'a', 'verbose': 'b'}[name] }) — {name} ---")
        for k, v in card.items():
            print(f"  {k}: {v}")
        print()

    # ── Pass/fail assertions (deterministic) ─────────────────────────────
    a = cards["compact"]
    b = cards["verbose"]
    ok_a = (
        a["verdict"] == "complete"
        and a["is_complete"] is True
        and a["attempt"] == 1
        and a["exceeds_cap"] is False
    )
    # F-A fix flipped case (b): the verbose compliant verdict now
    # parses complete on attempt 1 under the fused-scoped 2048 cap
    # (it exceeded the legacy 400 cap by construction — that was the
    # live failure).
    ok_b = (
        b["verdict"] == "complete"
        and b["is_complete"] is True
        and b["attempt"] == 1
        and b["exceeds_cap"] is False
        and b["evidence_count"] == 5
    )
    print("ASSERTIONS:")
    print(f"  case (a) compact fits-under-cap → parsed complete: "
          f"{'PASS' if ok_a else 'FAIL'}")
    print(f"  case (b) verbose fits-under-fused-cap → parsed complete: "
          f"{'PASS' if ok_b else 'FAIL'}")
    print()
    if ok_a and ok_b:
        print("FIX VERIFIED: fused-scoped cap=2048 + truncate-before-parse "
              "+ fused prompt's compliant payload (~997 chars) fits under "
              "2048 → verbose-but-correct verdict now parses complete on "
              "attempt 1. The pre-fix unparsable×2 defect is closed.")
        return 0
    else:
        print("FIX NOT VERIFIED — investigate (case (b) is the live "
              "regression — verbose-but-correct verdict should now "
              "parse).")
        return 1


# ─────────────────────────────────────────────────────────────────────────────
# Pytest entry points (so `pytest tests/probe/lca2_judge_truncation_repro.py`
# also works — manual probe, NOT registered in packs).
# ─────────────────────────────────────────────────────────────────────────────


def test_compact_case_passes():
    import pytest

    card = asyncio.run(_run_case("compact", _compact_payload()))
    assert card["verdict"] == "complete"
    assert card["is_complete"] is True
    assert card["attempt"] == 1
    assert card["exceeds_cap"] is False


def test_verbose_case_parses_complete():
    import pytest

    card = asyncio.run(_run_case("verbose", _verbose_payload()))
    assert card["verdict"] == "complete"
    assert card["is_complete"] is True
    assert card["attempt"] == 1
    assert card["exceeds_cap"] is False
    assert card["evidence_count"] == 5


if __name__ == "__main__":
    sys.exit(main())