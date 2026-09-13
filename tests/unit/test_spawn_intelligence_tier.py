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

import logging

import pytest

from daemon.config import _resolve_intelligence_tier_high_model
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
    the allowlist, mirroring
    :meth:`daemon.services.instance_lifecycle.InstanceLifecycleService._resolve_model_override`
    (case-insensitive canonical-name normalization against the
    allowlist). Tier literal is case-insensitive on
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
    ``("agentic", None)`` — pass-through, no WARN, no ERROR. Mirrors
    the
    :meth:`daemon.services.instance_lifecycle.InstanceLifecycleService._resolve_model_override`
    empty-allowed branch (early-return for None / whitespace model +
    empty allowlist pass-through). Loud-raise responsibility stays
    downstream — empty allowlist means no validation contract to
    enforce against.
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


# ─────────────────────────────────────────────────────────────────────────────
# Pin W1 — Boot-WARNING caplog pin (R-A6; phase1-plan task 2c)
#
# Pins the WARNING emission seam at daemon/config.py:3218-3224 so a future
# refactor cannot accidentally:
#   (a) swallow the WARNING (silent tier-mismatch on every boot),
#   (b) promote it to a raise (a single misconfigured env var would
#       brick the daemon at startup),
#   (c) leak the WARNING on the clean-default path (boot noise),
#   (d) break the empty=unset normalization contract that keeps the
#       documented ``"agentic"`` default authoritative.
#
# Mirrors the ``tests/unit/test_injected_notes_absorb_boot_validation.py``
# env-hygiene pattern (per-test monkeypatch + minimal tmp yaml) — see
# that file for the broader precedent. The WARNING is emitted via
# ``logger.warning(...)`` from the ``daemon.config`` logger, so
# ``caplog.at_level(logging.WARNING, logger="daemon.config")`` is the
# correct scope.
# ─────────────────────────────────────────────────────────────────────────────


_FLAG = "SPAWN_INTELLIGENCE_TIER_HIGH_MODEL"


def _write_yaml(tmp_path, *, allowed_models: list[str]) -> str:
    """Minimal loadable config.yaml — the boot WARNING only depends on
    the resolved ``llm.allowed_models`` and the env var; the rest of
    the config shape stays minimal so unrelated defaults stay inert."""
    text = f"""
llm:
  base_url: "https://api.openai.com/v1"
  api_key: "test-key"
  model: "agentic"
  allowed_models: {allowed_models!r}

persistence:
  db_path: "./data/instances.db"
"""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(text)
    return str(config_file)


class TestLoadConfigBootWarning:
    """Phase 5 follow-up: pin the R-A6 boot-WARNING contract end-to-end
    via ``load_config`` + ``caplog``. Per-test env hygiene via
    ``monkeypatch`` (matches ``test_llm_allowed_models_precedence`` /
    ``test_injected_notes_absorb_boot_validation`` precedent)."""

    @pytest.fixture(autouse=True)
    def _reset_spawn_intelligence_tier_boot_warned(self, monkeypatch):
        """Reset the module-level emit-once guard BEFORE each test so
        cross-test ordering doesn't pollute WARNING counts (D-1
        follow-up). Mirrors the sibling precedent at
        ``test_llm_allowed_models_precedence.py::TestWarnDeprecatedAllowedModelsGuard``
        — same module-level flag, same per-test reset, same idiom. The
        guard globals live in ``daemon.config`` module scope and leak
        across tests in a single pytest process; without this fixture,
        the first test to fire the WARNING consumes the guard and any
        later WARNING-asserting test silently sees zero records."""
        import daemon.config as cfg
        monkeypatch.setattr(
            cfg, "_spawn_intelligence_tier_boot_warned", False
        )

    def test_non_allowed_env_value_emits_one_warning(
        self, tmp_path, monkeypatch, caplog
    ):
        """(a) ``SPAWN_INTELLIGENCE_TIER_HIGH_MODEL="gpt-x"`` (NOT in
        ``allowed_models``) → ``load_config`` emits exactly ONE
        WARNING record. The record text carries (i) the
        ``[Config]`` log-forensics prefix, (ii) the field name
        ``spawn_intelligence_tier_high_model`` (the WARNING's
        subject — M9 single-source change), (iii) the resolved
        value, (iv) the allowed list. ``load_config`` MUST NOT
        raise (R-A6: WARNING, not boot-fail)."""
        monkeypatch.setenv(_FLAG, "gpt-x")
        from daemon.config import load_config

        with caplog.at_level(logging.WARNING, logger="daemon.config"):
            cfg = load_config(config_path=_write_yaml(tmp_path, allowed_models=["agentic", "coding"]))

        # No raise + resolved value is installed as the boot snapshot.
        assert cfg is not None
        assert cfg.llm.spawn_intelligence_tier_high_model == "gpt-x"

        records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        # M9: the WARNING now uses the field name as its subject
        # (not the env var name) and the ``[Config]`` log-forensics
        # prefix. Filter on the field-name substring to identify
        # the record; the env-var-name assertion (i) below was
        # re-framed to the field name per the M9 lockstep.
        tier_records = [
            r for r in records
            if "spawn_intelligence_tier_high_model" in r.getMessage()
        ]
        assert len(tier_records) == 1, (
            f"exactly ONE boot WARNING expected; got: "
            f"{[r.getMessage() for r in tier_records]}"
        )

        msg = tier_records[0].getMessage()
        # (i) [Config] log-forensics prefix (M9 — joins the
        # [Config] family at daemon/config.py:2259, :2318).
        assert "[Config]" in msg, (
            f"WARNING must carry the [Config] log-forensics prefix; got: {msg!r}"
        )
        # (ii) field name (M9 — subject is now the field name, not
        # the env var name).
        assert "spawn_intelligence_tier_high_model" in msg, (
            f"WARNING must name the field; got: {msg!r}"
        )
        # (iii) resolved value
        assert "gpt-x" in msg, (
            f"WARNING must carry the resolved value; got: {msg!r}"
        )
        # (iv) allowed list — accept either bracket-or-string repr
        # of the parsed CSV list (``['agentic', 'coding']`` from the
        # shared ``_parse_csv_or_json_list`` helper).
        assert "agentic" in msg and "coding" in msg, (
            f"WARNING must name the allowed list; got: {msg!r}"
        )

    def test_two_load_config_calls_emit_exactly_one_warning(
        self, tmp_path, monkeypatch, caplog
    ):
        """D-1 follow-up pin: ``load_config()`` runs twice on the boot
        path — ``daemon/api.py:245`` (lifespan startup) AND
        ``daemon/services/attestation_resolver.py:513`` (judge-model
        boot-log resolution). The mismatch WARNING must still emit
        EXACTLY ONCE across BOTH calls thanks to the module-level
        emit-once guard mirroring ``_allowed_models_deprecation_warned``
        at ``daemon/config.py:2309``. Ops grep-count WARNINGs as health
        signals; a permanent 2x count breaks exactly-N checks. Asserts:
        (i) NO raise across both calls (R-A6 — WARNING, not boot-fail);
        (ii) the resolved boot-snapshot is unchanged by the second call;
        (iii) EXACTLY ONE ``tier`` WARNING record accumulates across the
        two calls; (iv) the record still carries the M9 contract text
        (field-name subject + ``[Config]`` prefix + resolved value +
        allowed list). The autouse ``_reset_spawn_intelligence_tier_boot_warned``
        fixture guarantees the guard starts fresh per test, so this is a
        real exercise of the emit-once behavior — not a stale-flag
        shadow."""
        monkeypatch.setenv(_FLAG, "gpt-x")
        from daemon.config import load_config

        config_path = _write_yaml(tmp_path, allowed_models=["agentic", "coding"])
        with caplog.at_level(logging.WARNING, logger="daemon.config"):
            cfg1 = load_config(config_path=config_path)
            cfg2 = load_config(config_path=config_path)

        # (i) + (ii) — neither call raises; both return a resolved
        # boot-snapshot carrying the non-allowed env value.
        assert cfg1 is not None and cfg2 is not None
        assert cfg1.llm.spawn_intelligence_tier_high_model == "gpt-x"
        assert cfg2.llm.spawn_intelligence_tier_high_model == "gpt-x"

        records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        tier_records = [
            r for r in records
            if "spawn_intelligence_tier_high_model" in r.getMessage()
        ]
        # (iii) — the core D-1 pin: exactly ONE tier WARNING across two
        # load_config() invocations in the same process.
        assert len(tier_records) == 1, (
            f"exactly ONE boot WARNING expected across TWO load_config() "
            f"calls (D-1 emit-once guard); got "
            f"{len(tier_records)}: "
            f"{[r.getMessage() for r in tier_records]}"
        )

        # (iv) — M9 contract text preserved (regression pin against a
        # future refactor that touches the WARNING text or formatting).
        msg = tier_records[0].getMessage()
        assert "[Config]" in msg, (
            f"WARNING must carry the [Config] log-forensics prefix; got: {msg!r}"
        )
        assert "spawn_intelligence_tier_high_model" in msg, (
            f"WARNING must name the field; got: {msg!r}"
        )
        assert "gpt-x" in msg, (
            f"WARNING must carry the resolved value; got: {msg!r}"
        )
        assert "agentic" in msg and "coding" in msg, (
            f"WARNING must name the allowed list; got: {msg!r}"
        )

    def test_allowed_env_value_emits_no_warning(
        self, tmp_path, monkeypatch, caplog
    ):
        """(b) ``SPAWN_INTELLIGENCE_TIER_HIGH_MODEL="agentic"`` IS in
        ``allowed_models`` → ZERO WARNING records (clean boot). The
        WARNING is mismatch-only — a clean config must NOT generate
        log noise."""
        monkeypatch.setenv(_FLAG, "agentic")
        from daemon.config import load_config

        with caplog.at_level(logging.WARNING, logger="daemon.config"):
            cfg = load_config(config_path=_write_yaml(tmp_path, allowed_models=["agentic", "coding"]))

        assert cfg is not None
        assert cfg.llm.spawn_intelligence_tier_high_model == "agentic"

        tier_records = [
            r for r in caplog.records
            if r.levelno >= logging.WARNING
            and "spawn_intelligence_tier_high_model" in r.getMessage()
        ]
        assert tier_records == [], (
            f"clean config must not emit the tier-mismatch WARNING; "
            f"got: {[r.getMessage() for r in tier_records]}"
        )

    def test_env_unset_resolves_to_default(self, tmp_path, monkeypatch, caplog):
        """(c.1) Env var unset → resolved value is the documented default
        ``"agentic"`` (via the empty=unset normalization). Drives
        ``load_config`` end-to-end to keep the boot-snapshot + caplog
        contract honest. No WARNING emitted (the default IS in the
        allowlist)."""
        monkeypatch.delenv(_FLAG, raising=False)
        from daemon.config import load_config

        with caplog.at_level(logging.WARNING, logger="daemon.config"):
            cfg = load_config(config_path=_write_yaml(tmp_path, allowed_models=["agentic", "coding"]))

        assert cfg.llm.spawn_intelligence_tier_high_model == "agentic", (
            f"env unset must resolve to default 'agentic'; got: "
            f"{cfg.llm.spawn_intelligence_tier_high_model!r}"
        )
        tier_records = [
            r for r in caplog.records
            if r.levelno >= logging.WARNING
            and "spawn_intelligence_tier_high_model" in r.getMessage()
        ]
        assert tier_records == [], (
            f"default-in-allowlist must not warn; got: "
            f"{[r.getMessage() for r in tier_records]}"
        )

    def test_env_empty_string_normalizes_to_default(self, tmp_path, monkeypatch, caplog):
        """(c.2) Env var set to empty string → resolved value is the
        documented default ``"agentic"``. Bare ``SPAWN_INTELLIGENCE_TIER_HIGH_MODEL=``
        in ``.env`` reaches ``os.environ`` as ``""``; ``_clean_env_value``
        normalizes to UNSET (per the helper's contract — empty /
        whitespace-only → ``None``). Drives ``load_config`` end-to-end
        to keep the boot-snapshot contract honest."""
        monkeypatch.setenv(_FLAG, "")
        from daemon.config import load_config

        with caplog.at_level(logging.WARNING, logger="daemon.config"):
            cfg = load_config(config_path=_write_yaml(tmp_path, allowed_models=["agentic", "coding"]))

        assert cfg.llm.spawn_intelligence_tier_high_model == "agentic", (
            f"env empty string must normalize to default 'agentic'; got: "
            f"{cfg.llm.spawn_intelligence_tier_high_model!r}"
        )

    def test_resolver_helper_empty_value_normalizes_to_default(self):
        """(c.3) Direct call to the boot-snapshot resolver
        (``_resolve_intelligence_tier_high_model``) — pins the
        empty=unset normalization at the helper boundary so a future
        refactor cannot silently break it. ``None`` and ``""`` must
        BOTH return the documented default; a non-empty string must
        pass through trimmed."""
        assert _resolve_intelligence_tier_high_model(None) == "agentic"
        assert _resolve_intelligence_tier_high_model("") == "agentic"
        assert _resolve_intelligence_tier_high_model("   ") == "agentic"
        assert _resolve_intelligence_tier_high_model("gpt-5") == "gpt-5"
        assert _resolve_intelligence_tier_high_model("  gpt-5  ") == "gpt-5"
