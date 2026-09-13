"""Unit tests for the ``_resolve_intelligence_tier`` free function (Feature #1).

Phase 1 of the spawn-time intelligence override plan. Pins A-F + A2 cover
the 4-element return-tuple matrix from ``decisions.md`` D2:

    | tier              | resolved ∈ allowed | result                          |
    |-------------------|--------------------|---------------------------------|
    | ``None`` / empty  | n/a                | ``(None, None)`` — no override  |
    | ``"high"``        | yes                | ``(canonical, None)`` — OK      |
    | ``"high"``        | no                 | ``(resolved, "WARN: ..." )``    |
    | any other string  | n/a                | ``(None, "ERROR: ...")``        |

Plus A2: empty ``allowed_models`` is pass-through (no WARN, no ERROR).

Pure resolver — no DB, no manager, no LLM. ``uv run python -m pytest
tests/test_spawn_intelligence_tier.py -v`` from the worktree root.
"""

from __future__ import annotations

from daemon.services.instance_lifecycle import _resolve_intelligence_tier


# ─────────────────────────────────────────────────────────────────────────────
# Pin A — happy path: tier="high" + configured default in allowlist.
# ─────────────────────────────────────────────────────────────────────────────


def test_resolve_intelligence_tier_high_default_in_allowed():
    """Pin A (D2 success path): ``"high"`` + default config + ``"agentic"`` in
    ``allowed_models`` → ``("agentic", None)``. The configured tier-resolved
    model is returned alongside a clean (None) error string — the tool layer
    threads this directly into ``manager.spawn_instance(model=...)``.
    """
    resolved, err = _resolve_intelligence_tier(
        "high", allowed_models=("agentic", "coding")
    )
    assert resolved == "agentic"
    assert err is None


# ─────────────────────────────────────────────────────────────────────────────
# Pin B — WARN path: tier="high" but the resolved model is NOT in allowed.
# ─────────────────────────────────────────────────────────────────────────────


def test_resolve_intelligence_tier_high_not_in_allowed():
    """Pin B (D2 loud-validation contract): ``"high"`` + ``allowed_models=("coding",
    "coding2")`` (no ``"agentic"``) → ``("agentic", "WARN: ...")``. The WARN
    prefix is the loud-validation signal — the tool layer converts it into a
    raised ``ValueError`` (D2; Phase 2 task 4b.i).
    """
    resolved, err = _resolve_intelligence_tier(
        "high", allowed_models=("coding", "coding2")
    )
    assert resolved == "agentic"
    assert err is not None
    assert err.startswith("WARN:"), f"WARN prefix expected, got: {err!r}"
    assert "agentic" in err, f"resolved model name expected in WARN, got: {err!r}"
    assert "allowed_models" in err, f"allowed_models reference expected, got: {err!r}"


# ─────────────────────────────────────────────────────────────────────────────
# Pin C — No-op: tier is None → no override; legacy pool path stays active.
# ─────────────────────────────────────────────────────────────────────────────


def test_resolve_intelligence_tier_none_returns_no_override():
    """Pin C (D6 default-unchanged contract): ``tier=None`` → ``(None, None)``.
    The pure resolver short-circuits before touching ``allowed_models``;
    this is the no-override path that the tool layer never enters
    (the resolver block is guarded by ``if model_tier is not None``).
    """
    resolved, err = _resolve_intelligence_tier(
        None, allowed_models=("agentic",)
    )
    assert resolved is None
    assert err is None


# ─────────────────────────────────────────────────────────────────────────────
# Pin D — Defensive: empty-string tier treated as None.
# ─────────────────────────────────────────────────────────────────────────────


def test_resolve_intelligence_tier_empty_string_treated_as_none():
    """Pin D (defensive): ``tier=""`` → ``(None, None)``. Pydantic Literal won't
    allow empty string in production (the Literal gate rejects it before it
    reaches the tool body), but the free function must be robust for future
    internal callers that may not run Pydantic validation first.
    """
    resolved, err = _resolve_intelligence_tier(
        "", allowed_models=("agentic",)
    )
    assert resolved is None
    assert err is None


def test_resolve_intelligence_tier_whitespace_only_treated_as_none():
    """Pin D-extra: ``tier="   "`` (whitespace-only) → ``(None, None)``. Mirrors
    the empty-string case; defensive against accidental whitespace strings
    from upstream callers.
    """
    resolved, err = _resolve_intelligence_tier(
        "   ", allowed_models=("agentic",)
    )
    assert resolved is None
    assert err is None


# ─────────────────────────────────────────────────────────────────────────────
# Pin E — Unknown tier literal returns an error string (not None).
# ─────────────────────────────────────────────────────────────────────────────


def test_resolve_intelligence_tier_unknown_literal_returns_error():
    """Pin E (D11 future-proof): ``tier="bogus"`` → ``(None, "ERROR: ...")``.
    The function MUST NOT raise internally (the tool layer owns the raise);
    it returns an error string for the tool layer to convert into a
    ``ValueError``. The ERROR prefix is the prefix-conventional signal
    (Phase 2 task 4b.i grep-checks for ``"ERROR:"``).
    """
    resolved, err = _resolve_intelligence_tier(
        "bogus", allowed_models=("agentic",)
    )
    assert resolved is None
    assert err is not None
    assert err.startswith("ERROR:"), f"ERROR prefix expected, got: {err!r}"
    assert "bogus" in err, f"unknown literal name expected in ERROR, got: {err!r}"


# ─────────────────────────────────────────────────────────────────────────────
# Pin F — Case-insensitive canonical-name match.
# ─────────────────────────────────────────────────────────────────────────────


def test_resolve_intelligence_tier_high_case_insensitive_allowed_match():
    """Pin F (W7 parity): ``tier="HIGH"`` (uppercase tier literal) +
    ``allowed_models=("Agentic", "coding")`` (capitalized entry) →
    ``("Agentic", None)``. The resolver returns the canonical spelling from
    the allowlist, mirroring ``_resolve_model_override`` at
    ``instance_lifecycle.py:1267-1272``. Tier literal is case-insensitive on
    the input side too (defensive — Pydantic Literal gates exact match in
    production, but the free function is robust).
    """
    resolved, err = _resolve_intelligence_tier(
        "HIGH", allowed_models=("Agentic", "coding")
    )
    assert resolved == "Agentic"  # canonical (allowlist) spelling
    assert err is None


def test_resolve_intelligence_tier_returns_canonical_from_mixed_case_allowlist():
    """Pin F-extra: ``"high"`` + ``("AGENTIC", "coding")`` → ``("AGENTIC", None)``.
    Confirms the canonical name comes from the allowlist (not the configured
    default), preserving the W7 contract for downstream persistence.
    """
    resolved, err = _resolve_intelligence_tier(
        "high", allowed_models=("AGENTIC", "coding")
    )
    assert resolved == "AGENTIC"
    assert err is None


# ─────────────────────────────────────────────────────────────────────────────
# Pin A2 — Empty allowed_models is pass-through (W5; A2).
# ─────────────────────────────────────────────────────────────────────────────


def test_resolve_intelligence_tier_empty_allowed_models_passthrough():
    """Pin A2 (W5 / A2): ``tier="high"`` + ``allowed_models=()`` (empty) →
    ``("agentic", None)`` — pass-through, no WARN, no ERROR. Mirrors the
    ``_resolve_model_override`` empty-allowed branch at
    ``instance_lifecycle.py:1263-1265`` ("Empty list = unrestricted; pass
    through"). Loud-raise responsibility stays downstream — empty allowlist
    means no validation contract to enforce against.
    """
    resolved, err = _resolve_intelligence_tier("high", allowed_models=())
    assert resolved == "agentic"
    assert err is None


def test_resolve_intelligence_tier_empty_allowed_models_list_form():
    """Pin A2-extra: same as above but with a Python ``list`` (not tuple). The
    resolver signature accepts ``tuple[str, ...] | list[str] | None``; the
    empty-list path is the same as empty-tuple — confirms the type-dispatch.
    """
    resolved, err = _resolve_intelligence_tier("high", allowed_models=[])
    assert resolved == "agentic"
    assert err is None


# ─────────────────────────────────────────────────────────────────────────────
# Pin config — operator override of the boot-snapshot config field.
# ─────────────────────────────────────────────────────────────────────────────


def test_resolve_intelligence_tier_with_configured_model_override():
    """Operator override (Phase 5 Pin X parametrization preview): the tool
    layer reads ``manager.config.llm.spawn_intelligence_tier_high_model`` and
    passes it as ``configured_model``. This pin confirms the resolver honors
    the override — ``configured_model="gpt-5"`` + ``"gpt-5"`` in
    ``allowed_models`` → ``("gpt-5", None)`` (operator remap works end-to-end).
    """
    resolved, err = _resolve_intelligence_tier(
        "high",
        allowed_models=("agentic", "coding", "gpt-5"),
        configured_model="gpt-5",
    )
    assert resolved == "gpt-5"
    assert err is None


def test_resolve_intelligence_tier_with_configured_model_not_in_allowed():
    """Operator override + WARN: ``configured_model="gpt-5"`` but ``"gpt-5"``
    NOT in ``allowed_models`` → ``("gpt-5", "WARN: ...")``. The tool layer
    raises loud with the operator's configured value named (boot WARNING +
    per-spawn raise — see R-A6 + §2.1 verbatim error text).
    """
    resolved, err = _resolve_intelligence_tier(
        "high",
        allowed_models=("agentic", "coding"),
        configured_model="gpt-5",
    )
    assert resolved == "gpt-5"
    assert err is not None
    assert err.startswith("WARN:")
    assert "gpt-5" in err


# ─────────────────────────────────────────────────────────────────────────────
# Pin R — D6 re-pin: tier=None path is pure no-override (Phase 5 reference).
# ─────────────────────────────────────────────────────────────────────────────


def test_resolve_intelligence_tier_none_returns_no_override_d6_repin():
    """Pin R (D6 explicit re-pin, Phase 5): ``_resolve_intelligence_tier(None,
    allowed_models=("agentic",))`` returns ``(None, None)`` — same shape as
    Pin C, re-pinned in this phase for explicit D6 reference. The resolver
    short-circuits BEFORE touching ``allowed_models`` — no error string ever
    leaks on the no-override path.
    """
    resolved, err = _resolve_intelligence_tier(
        None, allowed_models=("agentic",)
    )
    assert resolved is None
    assert err is None
    # And the resolver does NOT raise.
    # (The function is documented pure; this assertion is defensive — if a
    # future refactor accidentally adds a raise, this pin catches it.)


# ─────────────────────────────────────────────────────────────────────────────
# Pin S — None path is pure (no side effects, no error leak).
# ─────────────────────────────────────────────────────────────────────────────


def test_resolve_intelligence_tier_high_then_none_pure():
    """Pin S (D6 unit): once the resolver returns ``(None, None)`` for
    ``None``, no error string ever leaks into the caller. Defensive — a
    ``WARN:`` or ``ERROR:`` prefix on the no-override path would be a
    silent breakage of the default-unchanged contract.
    """
    for allowed in [None, (), [], ("agentic",), ("agentic", "coding")]:
        _, err = _resolve_intelligence_tier(None, allowed_models=allowed)
        assert err is None, (
            f"resolver MUST NOT emit an error string on the None path "
            f"(allowed_models={allowed!r}); got: {err!r}"
        )
