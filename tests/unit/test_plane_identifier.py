"""Plane REST identifier derivation + env-quote sanitizer (D2 + D3 unit pins).

These tests are *pure* — they exercise the small helpers in
``daemon.clients.plane_http_client`` without spinning up the sync
service, the HTTP client, or any DB. They're the focused pins the
final-gate review called out:

* D2 — ``_env()`` strips ONE outer matching-quote pair; interior
  quotes preserved; unbalanced leaves value unchanged.
* D3 — ``derive_plane_identifier`` produces a Plane-valid
  ``identifier`` (≤12 chars, uppercase, [A-Z0-9] only) deterministically
  for the same ``(name, attempt)`` inputs.

The integration-level collision-retry behavior (the ``_create_or_adopt``
loop driving ``client.create_project`` retries on 409) lives in
``tests/unit/test_plane_sync_phase4.py::TestCreatePayloadIdentifier``
because it needs the sync service fixture surface.

Mocking surface mirrors ``tests/unit/test_plane_sync_phase3.py`` — no
real Plane calls. Verified rules (2026-09-20 live probe against
``plane.ensem.dev``):

* ``identifier`` is REQUIRED (400 ``{"identifier":["This field is required."]}``)
* Max 12 chars (400 ``{"identifier":["Ensure this field has no more than 12 characters."]}``)
* Server auto-uppercases; spaces preserved; we restrict to [A-Z0-9]
  for portability.
"""
from __future__ import annotations

import os

import pytest
from unittest.mock import AsyncMock, MagicMock

from daemon.clients.plane_http_client import (
    PlaneAPIError,
    PlaneAuthError,
    PlaneHttpClient,
    PlaneIdentifierCollisionError,
    PlaneNotFoundError,
    _env,
    derive_plane_identifier,
    sanitize_plane_name,
)


# ─────────────────────────────────────────────────────────────────────────────
# D2 — _env() quote-strip semantics
# ─────────────────────────────────────────────────────────────────────────────


class TestEnvQuoteStrip:
    """``_env()`` mirrors the Phase-1 sanitizer: one outer matching-quote
    pair is unwrapped; interior quotes preserved; whitespace trimmed
    outside the wrap.

    Why: the prod quote-leaking loader (Phase-2 probe evidence) emits
    values like ``"plane_api_xyz"`` — a slug or key with literal quote
    chars on either end yields 403 at the Plane API.
    """

    DQUOTE = '"'
    SQUOTE = "'"

    def test_double_quoted_value_is_stripped(self, monkeypatch):
        monkeypatch.setenv("TEST_VAR", f"{self.DQUOTE}abc{self.DQUOTE}")
        assert _env("TEST_VAR") == "abc"

    def test_single_quoted_value_is_stripped(self, monkeypatch):
        monkeypatch.setenv("TEST_VAR", f"{self.SQUOTE}xyz{self.SQUOTE}")
        assert _env("TEST_VAR") == "xyz"

    def test_clean_value_unchanged(self, monkeypatch):
        monkeypatch.setenv("TEST_VAR", "plain-value")
        assert _env("TEST_VAR") == "plain-value"

    def test_interior_quote_preserved(self, monkeypatch):
        """One outer pair unwrapped, interior double-quote survives.
        Plane API tokens can legitimately contain interior quotes
        (e.g. the prod key passed through a Python double-stringified
        shell variable)."""
        monkeypatch.setenv(
            "TEST_VAR", f'{self.DQUOTE}he said "hi" today{self.DQUOTE}'
        )
        assert _env("TEST_VAR") == 'he said "hi" today'

    def test_interior_single_quote_preserved(self, monkeypatch):
        monkeypatch.setenv(
            "TEST_VAR", f"{self.SQUOTE}it's fine{self.SQUOTE}"
        )
        assert _env("TEST_VAR") == "it's fine"

    def test_unbalanced_leading_quote_unchanged(self, monkeypatch):
        """Leading-quote with no matching trailer is NOT a wrapping
        pair — the loader probably mis-copied; do NOT silently strip."""
        monkeypatch.setenv("TEST_VAR", f"{self.DQUOTE}only-leading")
        assert _env("TEST_VAR") == f"{self.DQUOTE}only-leading"

    def test_unbalanced_trailing_quote_unchanged(self, monkeypatch):
        monkeypatch.setenv("TEST_VAR", f"only-trailing{self.DQUOTE}")
        assert _env("TEST_VAR") == f"only-trailing{self.DQUOTE}"

    def test_whitespace_outside_wrap_is_trimmed(self, monkeypatch):
        """Whitespace trimming is on the OUTSIDE — matches the
        pre-existing contract; the unwrap happens after strip()."""
        monkeypatch.setenv(
            "TEST_VAR", f"   {self.DQUOTE}  value  {self.DQUOTE}   "
        )
        assert _env("TEST_VAR") == "value"

    def test_only_quotes_yield_empty(self, monkeypatch):
        monkeypatch.setenv("TEST_VAR", f"{self.DQUOTE}{self.DQUOTE}")
        assert _env("TEST_VAR") == ""

    def test_missing_env_returns_empty(self, monkeypatch):
        monkeypatch.delenv("TEST_VAR", raising=False)
        assert _env("TEST_VAR") == ""


# ─────────────────────────────────────────────────────────────────────────────
# D3 — derive_plane_identifier pure-function contract
# ─────────────────────────────────────────────────────────────────────────────


class TestDerivePlaneIdentifier:
    """``derive_plane_identifier`` is a pure function — same
    ``(name, attempt)`` → same ``identifier``, across processes and
    restarts. The rule is verified against the live API on 2026-09-20:
    ``identifier`` is required, max 12 chars, uppercase, [A-Z0-9] safe."""

    def test_simple_uppercase_passthrough(self):
        assert derive_plane_identifier("Ensemble") == "ENSEMBLE"

    def test_lowercase_input_is_uppercased(self):
        assert derive_plane_identifier("ensemble") == "ENSEMBLE"

    def test_spaces_and_hyphens_stripped(self):
        """Existing workspace identifier ``LLMPROXY`` confirms the
        server canonicalizes to uppercase-alphanumeric with NO
        separators; spaces and hyphens are stripped."""
        assert derive_plane_identifier("LLM Proxy") == "LLMPROXY"
        assert derive_plane_identifier("agents-ensemble") == "AGENTSENSEMB"

    def test_truncation_at_twelve_chars(self):
        """13+ char base → first 12 chars only."""
        long = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        assert len(derive_plane_identifier(long)) == 12
        assert derive_plane_identifier(long) == "ABCDEFGHIJKL"

    def test_empty_name_falls_back_to_ensemble(self):
        """All-chars-stripped (empty/non-ASCII) → ``ENSEMBLE``
        fallback; the payload MUST always carry a non-empty
        ``identifier`` or Plane returns 400."""
        assert derive_plane_identifier("") == "ENSEMBLE"

    def test_non_ascii_name_falls_back_to_ensemble(self):
        """Non-ASCII letters (e.g. ``café`` → ``É`` after upper)
        collapse to ``""`` after the regex strip — substitute
        the fallback rather than ship an empty identifier."""
        assert derive_plane_identifier("café") == "CAF"
        # Pure-emoji name → all stripped → fallback:
        assert derive_plane_identifier("🎉") == "ENSEMBLE"

    def test_only_special_chars_falls_back(self):
        """Punctuation-only name → all stripped → fallback."""
        assert derive_plane_identifier("---___---") == "ENSEMBLE"

    def test_digits_preserved(self):
        assert derive_plane_identifier("proj123") == "PROJ123"

    def test_max_length_is_twelve_for_attempt_one(self):
        """First-attempt length is bounded by 12 regardless of input."""
        for n in ["a" * 1, "a" * 12, "a" * 100]:
            assert len(derive_plane_identifier(n)) <= 12

    # ── Determinism ──────────────────────────────────────────────────────

    def test_same_input_same_output_attempt_one(self):
        """Determinism contract: same (name, attempt) → same output
        across repeated calls and across processes/restarts."""
        for n in ["Ensemble", "LLM Proxy", "agents-ensemble", ""]:
            a = derive_plane_identifier(n, attempt=1)
            b = derive_plane_identifier(n, attempt=1)
            c = derive_plane_identifier(n, attempt=1)
            assert a == b == c

    def test_same_input_same_output_attempt_two(self):
        a = derive_plane_identifier("Ensemble", attempt=2)
        b = derive_plane_identifier("Ensemble", attempt=2)
        c = derive_plane_identifier("Ensemble", attempt=2)
        assert a == b == c

    def test_distinct_attempts_distinct_identifiers(self):
        """The suffix scheme MUST produce a different identifier for
        attempt=1 vs attempt=2 vs attempt=3 — otherwise the collision
        retry is a no-op."""
        assert (
            derive_plane_identifier("Ensemble", attempt=1)
            != derive_plane_identifier("Ensemble", attempt=2)
        )
        assert (
            derive_plane_identifier("Ensemble", attempt=2)
            != derive_plane_identifier("Ensemble", attempt=3)
        )

    def test_attempt_suffix_truncates_base_to_fit(self):
        """``ENSEMBLE`` (8) + suffix ``2`` = 9 chars; fits.
        For 11-char base + 1-char suffix = 12 (fits).
        12-char base + 1-char suffix → must TRUNCATE base."""
        # 12-char base, attempt 2 → 11 base + "2" = 12 total.
        twelve = "ABCDEFGHIJKL"
        assert len(derive_plane_identifier(twelve, attempt=2)) == 12
        assert derive_plane_identifier(twelve, attempt=2).endswith("2")

    def test_attempt_three_uses_three_char_suffix(self):
        """Suffix = decimal attempt number — always."""
        assert derive_plane_identifier("Ensemble", attempt=3).endswith("3")

    def test_attempt_ten_uses_two_char_suffix(self):
        """10 → 2 chars; ``ENSEMBLE`` (8) + ``10`` = 10 chars; fits."""
        result = derive_plane_identifier("Ensemble", attempt=10)
        assert result.endswith("10")
        assert len(result) <= 12

    def test_higher_attempts_still_within_twelve(self):
        """All reasonable attempts must stay within the 12-char cap."""
        for attempt in range(1, 200):
            result = derive_plane_identifier("Ensemble", attempt=attempt)
            assert len(result) <= 12, (attempt, result)

    def test_attempt_zero_raises(self):
        """Defensive: out-of-range ``attempt`` raises (no silent
        normalization)."""
        with pytest.raises(ValueError, match="attempt must be >= 1"):
            derive_plane_identifier("Ensemble", attempt=0)

    def test_negative_attempt_raises(self):
        with pytest.raises(ValueError, match="attempt must be >= 1"):
            derive_plane_identifier("Ensemble", attempt=-1)


# ─────────────────────────────────────────────────────────────────────────────
# Error hierarchy — typed exception for 409 collision detection
# ─────────────────────────────────────────────────────────────────────────────


class TestErrorHierarchy:
    """``PlaneIdentifierCollisionError`` is a subclass of
    ``PlaneAPIError`` so callers can catch either — and carries
    ``status_code`` so the retry layer doesn't have to parse the
    message text.
    """

    def test_identifier_collision_is_a_plane_api_error(self):
        err = PlaneIdentifierCollisionError("conflict", status_code=409)
        assert isinstance(err, PlaneAPIError)

    def test_status_code_is_carried(self):
        err = PlaneIdentifierCollisionError("conflict", status_code=409)
        assert err.status_code == 409

    def test_body_is_carried(self):
        err = PlaneIdentifierCollisionError(
            "conflict", status_code=409, body='{"identifier":["already taken"]}'
        )
        assert err.body == '{"identifier":["already taken"]}'

    def test_auth_error_is_a_plane_api_error(self):
        err = PlaneAuthError("auth", status_code=403)
        assert isinstance(err, PlaneAPIError)

    def test_not_found_error_is_a_plane_api_error(self):
        err = PlaneNotFoundError("404", status_code=404)
        assert isinstance(err, PlaneAPIError)

# ─────────────────────────────────────────────────────────────────────────────
# Name sanitization (prod hot-fix 2026-09-22)
# ─────────────────────────────────────────────────────────────────────────────


class TestSanitizePlaneName:
    """``sanitize_plane_name`` — Plane REJECTS project names containing
    characters outside ``[A-Za-z0-9 _]`` with HTTP 400
    ``{"non_field_errors":["Project name cannot contain special
    characters."]}`` (live-probed against plane.ensem.dev 2026-09-22:
    hyphen → 400, space → 201, underscore → 201, parens/unicode → 400).
    Ensemble names are commonly hyphenated (``agents-ensemble``), so
    every raw-name create 400'd and the boot sweep cycled.

    Pinned semantics: disallowed char RUNS collapse to a single space;
    ends stripped; empty input → deterministic fallback
    ``"Ensemble Project"``; output idempotent + deterministic.
    """

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            # The prod-bug shape: hyphenated ensemble name.
            ("agents-ensemble", "agents ensemble"),
            ("data-center-scripts", "data center scripts"),
            # Underscore and space are Plane-ACCEPTED → preserved.
            ("probe_under_score_del", "probe_under_score_del"),
            ("LLM Proxy", "LLM Proxy"),
            # Disallowed runs collapse to ONE space (no double-space).
            ("a--b (c) d", "a b c d"),
            # Non-ASCII letters each become a space; interior ASCII
            # letters survive ("ünï" → " n " → "n").
            ("probe (paren) ünï d", "probe paren n d"),
            # Ends stripped even when the raw starts/ends disallowed.
            ("-agents-", "agents"),
            # Tabs/newlines are disallowed chars → single space.
            ("a\tb\nc", "a b c"),
        ],
    )
    def test_matrix(self, raw, expected):
        assert sanitize_plane_name(raw) == expected

    def test_clean_name_is_noop(self):
        """Names already in the accepted set pass through unchanged —
        guarantees existing all-clean adoption targets keep matching."""
        for name in ("Ensemble", "NEA", "LLM Proxy", "Dashboard Frontend",
                     "yedda_raw_data"):
            assert sanitize_plane_name(name) == name

    @pytest.mark.parametrize("raw", ["", "   ", "---", "()[]{}", "üï"])
    def test_empty_result_falls_back_deterministically(self, raw):
        """Empty / all-disallowed input → the SAME fixed fallback every
        time (retries must converge to one Plane name, not flip-flop)."""
        assert sanitize_plane_name(raw) == "Ensemble Project"

    def test_deterministic(self):
        samples = ["agents-ensemble", "", "a--b", "Yedda Dashboard Provider"]
        for s in samples:
            assert sanitize_plane_name(s) == sanitize_plane_name(s)

    @pytest.mark.parametrize(
        "raw",
        ["agents-ensemble", "a -- b", "", "---", "x (y) z", "ünï"],
    )
    def test_idempotent(self, raw):
        """``sanitize(sanitize(x)) == sanitize(x)`` — output alphabet is
        a subset of the accepted set, so a second pass is a no-op. This
        is what makes create-then-update / retry flows converge."""
        once = sanitize_plane_name(raw)
        assert sanitize_plane_name(once) == once


class TestClientWireNameSanitized:
    """Touchpoint (a): the create/update PAYLOAD ``name`` is sanitized
    at the client boundary — the raw Ensemble name must never reach the
    wire, or Plane 400s (the exact prod failure).

    ``description`` is deliberately forwarded VERBATIM: its validation
    behavior was not independently pinned by the probes (the 400 body
    was name-driven ``non_field_errors``), so we do not sanitize what
    we have not proven Plane rejects.
    """

    def _client(self):
        breaker = MagicMock(
            can_execute=AsyncMock(return_value=True),
            record_success=AsyncMock(),
            record_failure=AsyncMock(),
        )
        client = PlaneHttpClient(
            base_url="https://plane.test/api/v1/workspaces/nea/projects/",
            api_key="test-key-value",
            workspace_slug="nea",
            breaker=breaker,
        )
        client._request = AsyncMock(return_value={"id": "plane-1"})
        return client

    @pytest.mark.asyncio
    async def test_create_payload_name_is_sanitized(self):
        client = self._client()
        await client.create_project(
            name="agents-ensemble", description="d", identifier="AGENTS"
        )
        body = client._request.await_args.kwargs["json_body"]
        assert body["name"] == "agents ensemble"
        assert body["identifier"] == "AGENTS"

    @pytest.mark.asyncio
    async def test_update_payload_name_is_sanitized(self):
        """The UPDATE path (linked projects' steady-state sync AND the
        adoption-path update) sends the same wire field — a raw
        hyphenated name here 400s identically to create."""
        client = self._client()
        await client.update_project("plane-1", name="data-center-scripts")
        body = client._request.await_args.kwargs["json_body"]
        assert body["name"] == "data center scripts"

    @pytest.mark.asyncio
    async def test_description_forwarded_verbatim(self):
        """Documented disposition: description is NOT sanitized —
        Plane-side description validation is unproven (probe P4's 400
        was name-field-driven), so we leave it untouched."""
        client = self._client()
        tricky = "special descr (paren) hyphen-x & ünïcode"
        await client.create_project(name="agents-ensemble", description=tricky)
        body = client._request.await_args.kwargs["json_body"]
        assert body["description"] == tricky
        assert body["name"] == "agents ensemble"
