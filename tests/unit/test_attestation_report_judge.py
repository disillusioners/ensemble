"""Fused-judge service unit tests — the surviving post-Stage-3 surface.

Stage 3 (2026-09-17, resolver-unification R7): the two legacy window
judge entry points (``judge_completion_report_async`` /
``judge_completion_report_sync``) and their helpers
(``_slice_judge_window`` / ``_format_window_for_judge`` /
``_parse_judge_response`` / ``JudgeResult`` / ``JUDGE_SYSTEM_PROMPT``
/ the legacy caps) were DELETED along with their graph-node call
sites. The fused judge's behavioral coverage (verdicts, retry-once,
truncation, kill-switch) lives in
``tests/unit/test_attestation_fused_judge.py`` +
``tests/unit/test_attestation_fused_judge_truncation.py``; the
retirement itself is pinned by
``tests/unit/test_attestation_stage3_census.py``. This file keeps the
SURVIVING shared-surface tests: ``resolve_judge_model`` (model
resolution) and the excerpt-shaping helpers (secret redaction /
truncation — the incident-98b59dd7 forensic surface the fused judge
reuses verbatim).
"""

from __future__ import annotations

import pytest

from daemon.services import attestation_report_judge as judge_mod
from daemon.services.attestation_report_judge import (
    JUDGE_EXCERPT_MAX_CHARS,
    JUDGE_TIMEOUT_S,
    _redact_secrets,
    _shape_unparsable_excerpt,
    _truncate_excerpt,
    resolve_judge_model,
)


class _FakeConfig:
    """Minimal Config stand-in exposing ``llm.model_keywords``/``llm.model``."""


class _FakeLLM:
    model = "fake-main"
    model_keywords = "fake-quick"


def _fake_config(model: str = "fake-main", model_keywords: str | None = "fake-quick") -> _FakeConfig:
    cfg = _FakeConfig()
    cfg.llm = _FakeLLM()
    cfg.llm.model = model
    cfg.llm.model_keywords = model_keywords
    return cfg


# ─────────────────────────────────────────────────────────────────────────────
# resolve_judge_model — honors OPENAI_MODEL_KEYWORDS with fallback
# ─────────────────────────────────────────────────────────────────────────────


def test_resolve_judge_model_uses_model_keywords_when_set():
    """``model_keywords`` non-empty ⇒ that wins (operator intent)."""
    cfg = _fake_config(model="fake-main", model_keywords="quick")
    assert resolve_judge_model(cfg) == "quick"


def test_resolve_judge_model_falls_back_to_main_model_when_keywords_empty():
    """Empty ``model_keywords`` ⇒ falls back to ``model``."""
    cfg = _fake_config(model="fake-main", model_keywords="")
    assert resolve_judge_model(cfg) == "fake-main"


def test_resolve_judge_model_falls_back_when_keywords_none():
    """``None`` ``model_keywords`` ⇒ falls back to ``model``."""
    cfg = _fake_config(model="fake-main", model_keywords=None)
    assert resolve_judge_model(cfg) == "fake-main"


def test_resolve_judge_model_strips_whitespace_from_keywords():
    """Whitespace-only ``model_keywords`` ⇒ falls back to ``model``."""
    cfg = _fake_config(model="fake-main", model_keywords="   ")
    assert resolve_judge_model(cfg) == "fake-main"


# ─────────────────────────────────────────────────────────────────────────────
# Excerpt shaping — secret redaction + truncation (98b59dd7 forensic surface)
# ─────────────────────────────────────────────────────────────────────────────


def test_redact_secrets_redacts_bearer_token():
    """Bearer-style tokens are redacted before logging."""
    from daemon.services.attestation_report_judge import _redact_secrets

    text = (
        "The auth was bearer abcdefghijklmnopqrstuvwxyz1234 in the header."
    )
    out = _redact_secrets(text)
    assert "abcdefghijklmnopqrstuvwxyz1234" not in out
    assert "[REDACTED]" in out


def test_redact_secrets_redacts_api_key_shapes():
    """API-key-shaped strings are redacted before logging."""
    from daemon.services.attestation_report_judge import _redact_secrets

    text = (
        "curl -H 'api_key=abcdefgh1234567890' "
        '{"api-key": "abcdefgh1234567890"} '
        "and token=abcdefgh1234567890"
    )
    out = _redact_secrets(text)
    # All three secret-shaped strings are gone.
    assert "abcdefgh1234567890" not in out
    assert out.count("[REDACTED]") == 3


def test_redact_secrets_redacts_secret_shapes():
    """Generic secret-shaped strings are redacted."""
    from daemon.services.attestation_report_judge import _redact_secrets

    text = "Configured secret=abcdefgh1234567890 for the run."
    out = _redact_secrets(text)
    assert "abcdefgh1234567890" not in out
    assert "[REDACTED]" in out


def test_redact_secrets_preserves_short_tokens():
    """Tokens below the 8-char minimum are NOT redacted (avoid
    false positives on common prose words like 'token economy')."""
    from daemon.services.attestation_report_judge import _redact_secrets

    text = "token economy and bearer of good news"
    out = _redact_secrets(text)
    # The short 'bearer' is matched but no token-shaped secret is
    # present; the prose shape stays intact (no [REDACTED]).
    assert out == text


def test_redact_secrets_preserves_prose_after_hyphen_glued_run():
    """Hyphen-glued run: prose after whitespace survives redaction.

    The whole hyphen-joined run (``abc-def-ghi-jkl-mnop``) is treated
    as ONE secret-shaped run — the class deliberately includes
    ``-``/``.``/``_`` because real tokens (base64url, UUID-ish) contain
    them; splitting at glue chars would leak real token tails. Prose
    separated from the run by whitespace IS preserved (98b59dd7
    review Finding #1).
    """
    from daemon.services.attestation_report_judge import _redact_secrets

    text = "bearer abc-def-ghi-jkl-mnop standing in the middle of prose"
    out = _redact_secrets(text)
    assert out == "[REDACTED] standing in the middle of prose"


def test_redact_secrets_restores_trailing_punctuation_after_secret():
    """Run-final glue (``-``/``.``/``_``) is re-emitted after the sentinel.

    A secret at the end of a clause (``api_key=<token>.``) used to
    swallow the sentence-final dot into ``[REDACTED]``; the dot is
    glue, not secret material, so it is restored after the sentinel
    (98b59dd7 review Finding #1, dot-joined degradation).
    """
    from daemon.services.attestation_report_judge import _redact_secrets

    text = "with api_key=abcdefgh1234567890. Next sentence intact."
    assert _redact_secrets(text) == "with [REDACTED]. Next sentence intact."

    hyphen = "env token=abcdefgh1234567890- is set here"
    assert _redact_secrets(hyphen) == "env [REDACTED]- is set here"


def test_redact_secrets_glued_no_delimiter_run_redacts_whole_run():
    """Glued run with NO delimiter → full-run redaction (safe-by-default).

    In ``bearer <token>then_more_text_no_space`` the boundary between
    secret and prose is UNDETECTABLE — every char is class-valid, so
    any split point is a guess that could leak a real secret. The
    whole run is redacted; prose separated from the run by whitespace
    still survives. Deliberate: do NOT "fix" this by guessing. (A
    trailing ``(?!\\w)`` lookahead is a verified no-op here — the run
    already ends at whitespace/EOL where the lookahead holds.)
    """
    from daemon.services.attestation_report_judge import _redact_secrets

    text = "bearer abcdefgh1234567890then_more_text_no_space"
    assert _redact_secrets(text) == "[REDACTED]"

    # Dot/underscore-glued continuations behave the same way.
    assert (
        _redact_secrets("bearer abcdefgh1234567890.Then prose keeps going")
        == "[REDACTED] prose keeps going"
    )
    assert (
        _redact_secrets("bearer abcdefgh1234567890_then_more_prose_words")
        == "[REDACTED]"
    )


def test_truncate_excerpt_caps_at_max_chars():
    """Excerpt is capped at ``JUDGE_EXCERPT_MAX_CHARS``."""
    from daemon.services.attestation_report_judge import (
        _truncate_excerpt,
    )

    long_text = "x" * 1000
    out = _truncate_excerpt(long_text)
    assert len(out) == 400
    assert out.endswith("[truncated]")


def test_truncate_excerpt_collapses_whitespace():
    """Whitespace runs are collapsed (a runaway LLM that emits 1000
    newlines does not stretch the log row)."""
    from daemon.services.attestation_report_judge import (
        _truncate_excerpt,
    )

    text = "a\n\n\nb\t\tc   d"
    out = _truncate_excerpt(text)
    assert out == "a b c d"


def test_truncate_excerpt_handles_empty_input():
    from daemon.services.attestation_report_judge import (
        _truncate_excerpt,
    )

    assert _truncate_excerpt("") == ""
    assert _truncate_excerpt("   ") == ""


def test_shape_unparsable_excerpt_composes_redact_then_truncate(monkeypatch):
    """End-to-end: shape_unparsable_excerpt redacts AND truncates.

    Pinned: the shape is the composition of redact → truncate (the
    canonical log-row excerpt surface). Order matters — redaction
    first operates on the raw value so we don't lose secret markers
    to whitespace collapse; truncation then bounds the size for
    log-row grep-friendliness.
    """
    from daemon.services.attestation_report_judge import (
        _shape_unparsable_excerpt,
    )

    # Construct an input that exercises both helpers: a secret token
    # embedded in long prose that overflows the cap.
    token = "abcdefgh1234567890"
    text = (
        "Bearer " + token + " here\n\n" + ("y" * 500)
    )
    out = _shape_unparsable_excerpt(text)
    # Token redacted.
    assert token not in out
    assert "[REDACTED]" in out
    # Whitespace collapsed (the double-newline becomes a single space).
    assert "\n" not in out
    # Capped at JUDGE_EXCERPT_MAX_CHARS.
    assert len(out) == 400
    # Truncation tail marker present.
    assert out.endswith("[truncated]")


